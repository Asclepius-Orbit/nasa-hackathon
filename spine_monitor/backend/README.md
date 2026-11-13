**English** · [Español](README.es.md) · [Català](README.ca.md)

# spine-backend (the on-board app)

With the v1 contracts the app owns the stateful part: it listens to the bus, stores everything in
SQLite, sends the commands that drive the protocol, and calls the stateless engine.

## What it does
- **Bus ingestion** (`ingest.py`): subscribes with QoS 1 to every `topics.SUBSCRIBE_ALL_*` (streams,
  status, acks, clock anchors, calibrations). Each message is validated with
  `spine_contracts.validation` (full JSON Schema, including the `if/then` rules Pydantic misses), and
  its ids must match its topic. Invalid messages go to `rejected_messages` (bounded); ingestion never
  stops. Chunks are deduplicated by stream + `boot_id` + `seq`.
- **Storage** (`db.py`): sessions, chunks, calibrations, clock anchors, device status, commands and
  acks, feature sets, baselines, assessments, profile, self-reports and daily context. Each entity is
  stored exactly as its schema defines it.
- **Commands** (`control.py`): `start_session`, `stop_session`, `mark_event`, `calibrate`, published
  on `.../cmd` and tracked through their acks (`accepted` -> `completed`/`failed`, `rejected`,
  or `timeout` locally). Acks update the session (running, ended, events, calibration ids).
  **Restart recovery**: at startup a `sent` command past `ack_timeout_s` becomes `timeout`; the others get
  their timer re-armed for the remaining time. A `starting` session whose `start_session` expired, or
  that has no pending command, becomes `rejected` with `ack_timeout` (a late `accepted` still revives
  it). A session a dead process left `processing` goes back to `ended`, and every `ended` session is
  processed in the background: startup never waits for the engine.
- **Engine calls** (`analysis.py`): when a session ends, `/features`. When it is pre-flight,
  `/baseline` with every pre-flight feature set (new version each time). Then `/assess` with profile,
  baseline, recent feature sets, self-reports and daily context. Every self-report also triggers
  `/assess`. A stored profile or daily context, or a new baseline, asks for a reassessment debounced per
  astronaut (`REASSESS_DEBOUNCE_S`, 5 s after the last request; a burst makes one, and an immediate
  `/assess` in between replaces it). A background loop (`assessment-refresh`, every `REASSESS_TICK_S`,
  1 h) asks for one for every astronaut with a profile whose latest assessment is missing or older than
  `STALE_ASSESSMENT_HOURS`; one that cannot be assessed is retried on the next tick. A missing profile, baseline or feature set skips the assessment and says why. Absence is
  never turned into a green. A `/features` failure because the engine is unreachable or answers 5xx is
  retried with exponential backoff (`PROCESSING_RETRY_MIN_S` to `PROCESSING_RETRY_MAX_S`), up to
  `PROCESSING_MAX_ATTEMPTS` attempts, counted in SQLite (`processing_attempts`) so a restart goes on
  with the count. 4xx, contract errors and sessions without chunks are final (`processing_failed`).
- **Pseudonymous ids**: `AST-01`-style ids only. Commands carry only the schema fields (which allow no
  others). The profile is personal data and never goes on the bus.

## UI API (`/ui`, `api.py`)
Write side for the UI. Separate from the read-only API of `contracts/app-openapi.yaml` used by the MCP
server (see below). The app fills ids, `schema_version`, instants and `source`, then validates the entity against
its schema. Errors are `app/error` problem details.

Every `/ui` request needs `Authorization: Bearer <token>` (401 otherwise) and, when it is about one astronaut (its path names
the astronaut, or a session or command of theirs), `X-Astronaut-Id` with the astronaut unlocked in the UI: another one is a
403 problem details. `GET /ui/crew` and `PUT /ui/crew/{id}` need the token only. The token is `SPINE_APP_UI_TOKEN`, or the
content of `SPINE_APP_UI_TOKEN_FILE` (default `ui-token` next to the database), generated there with owner-only permissions on
the first start without one. `/health` and the read-only API need no token.

| Method | Path | What |
|---|---|---|
| PUT | `/ui/crew/{astronaut_id}` | Registers an id the UI assigned (no body: names and PINs stay in the UI). 201 new, 200 already registered |
| GET | `/ui/crew` | Ids known to the backend (registered or with data) and whether each has a profile and a baseline, and how many sessions (`ui-crew`, `ui-openapi.yaml`) |
| GET | `/ui/crew/{astronaut_id}/devices` | Latest status of each device |
| POST/GET | `/ui/crew/{astronaut_id}/contacts` | Calls to the crew medical officer (`domain/medical-contact`: who, when, optional note). Each one requeues the ground summary of its day, which carries the code and the instant, never the note |
| PUT/GET | `/ui/crew/{astronaut_id}/profile` | Astronaut profile |
| PUT/GET | `/ui/crew/{astronaut_id}/daily-context/{date}` | Daily context |
| POST | `/ui/crew/{astronaut_id}/self-reports` | Daily, weekly ODI or red flag (with `other_symptoms`: `very_severe_pain`, `other`, kept apart from the red flags until Aguado decides how they are triaged); returns the report and the new assessment (or why it was skipped). Same `report_id` again is idempotent |
| GET | `/ui/crew/{astronaut_id}/self-reports/due` | Whether the daily report and the weekly ODI are due; red flags are always available. The Oswestry is stored and shown (Oswestry screen, history) but does not score risk: the engine, the risk model and the rules ignore it. Whether it goes in the Houston summary is pending (#85); `ground-summary` has no ODI field for now |
| POST | `/ui/crew/{astronaut_id}/sessions` | Session plan (device, type, protocol, streams, load...) -> `start_session`. `mission_context` defaults to today's daily context |
| GET | `/ui/sessions/{session_id}` | Session, state (`starting`, `running`, `ended`, `processing`, `processed`, `processing_failed`, `rejected`) and feature set |
| POST | `/ui/sessions/{session_id}/reprocess` | Only for `processing_failed` (409 otherwise, 404 unknown): resets the attempts, moves it to `ended` and processes it in the background. 202 `{"session": id, "state": "ended"}`. The only way to re-run a session that failed for good or before attempts were tracked |
| POST | `/ui/sessions/{session_id}/events` | `mark_event` |
| POST | `/ui/sessions/{session_id}/calibrations` | `calibrate` |
| POST | `/ui/sessions/{session_id}/stop` | `stop_session` with a reason. `aborted_pain` takes an optional `rep_index` (422 with any other reason): the session gets a `pain_reported` event with the repetition and the instant, the ground summary of the day shows it (`pain_stop`) and an /assess is requested |
| GET | `/ui/sessions/{session_id}/reps` | Server-sent events with the live repetition counter (`ui-openapi.yaml`): `state` at each change, `end` when the session ends |
| GET | `/ui/commands/{command_id}` | Command state and acks (the UI polls it to move to the next step) |
| GET | `/ui/crew/{astronaut_id}/houston-report.pdf?end=YYYY-MM-DD` | PDF report for Houston of the last 7 UTC days ending on `end` (default today; 422 `invalid_range` if in the future). `Content-Disposition: attachment; filename="houston-report-<id>-<start>_<end>.pdf"`. Only that week: daily reports, red flags and urgent symptoms as codes (never free-text notes), latest Oswestry (informative), sessions with their measures and the engine assessments (level, confidence, drivers, `REC_*`, escalation, `RULE_*`). No MCP or agent text and no profile data, only the pseudonymous id. An empty week still gives a PDF that says `No data` (never green). Built in `monitor/report/` (`week.py` pure data, `charts.py` matplotlib/Agg, `houston_pdf.py` reportlab) |
| GET | `/health` | Liveness, MQTT connection and message counters |

## Read-only API for the MCP server (`readonly.py`)
`contracts/app-openapi.yaml`, served by its own FastAPI app on port 8002 (compose service `read-api`), so
the MCP server never reaches the `/ui` write endpoints. It opens the same SQLite with
`PRAGMA query_only`: only `GET`, and the connection cannot write.

- `/crew/{astronaut_id}/status` (`get_current_status`): latest assessment, session and self-report,
  devices, active baseline and freshness. Missing data is `null` with `stale: true`; thresholds are
  `SPINE_APP_STALE_*_HOURS`.
- `/crew/{astronaut_id}/history?from&to&limit` (`get_history`, and twice for `compare_periods`): feature
  sets by the start of their session, assessments by `as_of`.
- `/assessments/{assessment_id}/explanation` (`explain_assessment`): the assessment with its baseline
  entries (drivers only), feature sets and self-reports resolved; anything gone is in `unresolved`.
- `/crew/{astronaut_id}/assess-base` (`simulate_scenario`): the /assess request the app would send now (profile,
  latest baseline, and the feature sets, self-reports and daily context of the last `assess_window_days`, with
  `as_of` = the app's clock), built by the same code as the real assessment and never sent. Unknown astronaut, or
  nothing to build it from, is `404 not_found` with `detail` `no_profile`, `no_baseline` or `no_feature_sets`.
- Resources: one assessment, session (only sessions that really ran), session feature set, self-report or
  baseline, and lists of assessments, sessions and self-reports of a window.
- Windows are inclusive, RFC 3339 with offset; `to` before `from` is `invalid_range`. Lists are oldest
  first, capped by `limit` (1-500, default 100), keeping the most recent and setting `truncated`.
- An unknown astronaut or entity is `404 not_found`; errors are `app/error` problem details.
- Tested against the contract with `openapi_drift` and fuzzed with Schemathesis.

Run it alone: `uv run uvicorn --factory monitor.readonly:create_read_app --port 8002`.

## Database: migrations, backups, retention and export

- The schema is versioned (`PRAGMA user_version`, `STORE_SCHEMA_VERSION` in `db.py`). An older file is first copied with the
  sqlite3 backup API to `backups/<name>.v<N>.<timestamp>.db` and then migrated step by step (`MIGRATIONS[n]`: n -> n+1) in
  one transaction with the version bump: a failed migration leaves it as it was. A newer file, or a foreign one, is refused.
- WAL. The read-only API (query-only connection) never writes and never migrates: it refuses a file the backend has not
  migrated yet, and reads what the backend writes while both are open.
- Every `SPINE_APP_BACKUP_INTERVAL_S` (default a day) a copy goes to `SPINE_APP_BACKUP_DIR` (default `backups/` next to the
  database, so in the `backend_data` volume), keeping the newest `SPINE_APP_BACKUP_KEEP` (7). After that copy, the raw chunks of
  the `processed` sessions started more than `SPINE_APP_CHUNK_RETENTION_DAYS` (30; `=null` keeps them) ago are deleted and the
  session is marked (`chunks_purged_at`). Feature sets, baselines, assessments and self-reports are never deleted.
- `GET /ui/crew/{astronaut_id}/export`: everything stored for the astronaut (profile, daily contexts, sessions with their
  state, feature sets, baselines, assessments, self-reports, medical contacts, device statuses) except the raw chunks.

## Running it
```
uv run uvicorn --factory monitor.main:create_app --port 8000
```
Settings are `SPINE_APP_*` variables (`settings.py`): `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_TLS`, `MQTT_TLS_CA_FILE`, `ENGINE_URL`, `DB_PATH`,
`ACK_TIMEOUT_S`, `ASSESS_WINDOW_DAYS`, `STORE_UNKNOWN_SESSION_CHUNKS`, `LIVE_NO_SIGNAL_S`, `LIVE_HEARTBEAT_S`, `LIVE_TICK_S`, `WEEKLY_ODI_INTERVAL_DAYS`, `REASSESS_DEBOUNCE_S`, `REASSESS_TICK_S`. In
Docker Compose the database lives in the `backend_data` volume (`/data/spine_monitor.db`).

Analysis retries, also `SPINE_APP_*`:

| Variable | Default | Meaning |
|---|---|---|
| `PROCESSING_RETRY_MIN_S` | 30 | Delay before the first retry of a transient `/features` failure; it doubles per failure. |
| `PROCESSING_RETRY_MAX_S` | 900 | Longest delay; not below the minimum. |
| `PROCESSING_MAX_ATTEMPTS` | 5 | Attempts before the session stays `processing_failed`. |
| `PROCESSING_RETRY_TICK_S` | 5 | How often the retrier looks for an elapsed backoff. |

Ground link: every assessment queues the summary of its UTC day in SQLite (`ground_outbox`), and a background task sends it, over a
simulated transport, while the link is up; `GET /ui/crew/{id}/houston` and `/outbox` show it. A failed send is retried with a backoff
per entry and no limit of attempts; a restart goes on from SQLite. Settings, also `SPINE_APP_*`; a bad value stops the start:

| Variable | Default | Meaning |
|---|---|---|
| `GROUND_WINDOW_ANCHOR` | unset | Start of a window, RFC 3339 with offset. Unset: no link configured, `ground_link` is null and nothing is sent. |
| `GROUND_WINDOW_PERIOD_MIN` | 90 | Minutes between the start of two windows. |
| `GROUND_WINDOW_UP_MIN` | 10 | Minutes the link is up in each window; below the period. |
| `GROUND_RETRY_MIN_S` | 1 | Delay after the first failure of an entry; it doubles per failure. |
| `GROUND_RETRY_MAX_S` | 300 | Longest delay; not below the minimum. |
| `GROUND_TICK_S` | 1 | How often the sender looks for an opened window or an elapsed backoff. |
| `GROUND_SEND_TIMEOUT_S` | 30 | Longest wait for the ground to answer one send; past it the send fails with code `timeout` and backs off. |

## MQTT broker: users, ACL and LAN access
The on-board Mosquitto (`app/docker-compose.yml`) refuses anonymous clients: every client logs in and only reaches the
topics of its ACL (`app/mosquitto/acl.template` and `acl-device.template`, from the topics of `contracts/asyncapi.yaml`).
No credential is in the repo. Before the first `docker compose` command, from the repo root (Linux, macOS or Git Bash on
Windows; it only needs docker):
```bash
sh app/mosquitto/setup.sh
```
It writes random passwords to `app/.env` and generates `app/mosquitto/auth/passwd` (hashed with `mosquitto_passwd` inside
the broker image) and `app/mosquitto/auth/acl`; all of them are gitignored, and every compose command fails with
`run sh app/mosquitto/setup.sh` until they exist. Running it again keeps the passwords; `--force` rotates them. After any
change recreate the broker (`docker compose -f app/docker-compose.yml up -d --force-recreate mosquitto`) and, after a
rotation, the clients. CI generates throwaway ones on every run.

| User | Who | Rights |
|---|---|---|
| `spine-app` | backend | reads `stream/+`, `status`, `ack`, `clock` and `calibration/+` of every device; writes every `cmd` |
| `spine-health` | compose healthcheck | reads `$SYS/broker/uptime` |
| `spine-itest` | `mosquitto` tests | the same as `spine-app` |
| `<device_id>` | one device: `sim-01`, `sim-02` (simulator), `itest-01`, `itest-02` (tests), the real ones | under `asclepius/v1/crew/<astronaut_id>/device/<device_id>/` only: writes `stream/+`, `status`, `ack`, `clock`, `calibration/+`; reads `cmd` |

A publish the ACL denies is dropped silently, and a denied subscription receives nothing. The backend settings are
`SPINE_APP_MQTT_USERNAME`, `SPINE_APP_MQTT_PASSWORD` (never logged or shown), `SPINE_APP_MQTT_TLS` (`true` to connect over
TLS) and `SPINE_APP_MQTT_TLS_CA_FILE` (the CA that signed the broker certificate; unset: the system CAs). Compose sets the
first two from `app/.env`.

### Registering a device
```bash
sh app/mosquitto/setup.sh --device AST-03:wb-01
docker compose -f app/docker-compose.yml up -d --force-recreate mosquitto
```
The user is the device id (`wb-01`) and its password is `SPINE_MQTT_DEVICE_PASSWORD_WB_01` in `app/.env`; configure both on
the device's gateway (for the simulator gateway, `SPINE_SIM_MQTT_USERNAME` and `SPINE_SIM_MQTT_PASSWORD`). Ids follow
`spine_contracts.topics` (`AST-NN`, `[a-z0-9][a-z0-9_-]{1,31}`); with just `wb-01` the device may publish for any astronaut.
To remove one, delete it from `SPINE_MQTT_DEVICES` and its password line in `app/.env`, run the script again and recreate
the broker.

### LAN access over TLS
Port 1883 (plain MQTT) is published only on `127.0.0.1`. For a gateway on another machine, `app/docker-compose.lan.yml` adds
a TLS listener on 8883, with the same users and ACL, published on one host address and with certificates mounted from a
folder outside the repo (never commit them):
```bash
export SPINE_MQTT_TLS_DIR="$HOME/spine-mqtt-certs" SPINE_MQTT_LAN_ADDRESS=192.168.1.20
docker compose -f app/docker-compose.yml -f app/docker-compose.lan.yml up -d mosquitto
```
The folder holds `ca.crt`, `server.crt` and `server.key`. A private CA and a certificate for that address:
```bash
mkdir -p "$SPINE_MQTT_TLS_DIR" && cd "$SPINE_MQTT_TLS_DIR"
openssl req -x509 -newkey rsa:3072 -nodes -days 365 -keyout ca.key -out ca.crt -subj "/CN=spine-mqtt-ca"
openssl req -newkey rsa:3072 -nodes -keyout server.key -out server.csr -subj "/CN=spine-broker"
printf 'subjectAltName=IP:%s\n' "$SPINE_MQTT_LAN_ADDRESS" > san.ext
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 365 -extfile san.ext -out server.crt
chmod 600 ca.key server.key
```
Keep `ca.key` off the station once the certificate is signed. The gateway connects to that address on port 8883 with TLS on
and `ca.crt` as its CA (`SPINE_SIM_MQTT_TLS=true`, `SPINE_SIM_MQTT_TLS_CA_FILE=ca.crt`).

## TypeScript types for the UI
`app/ui/src/contracts/v1.ts` is generated from `contracts/schemas/v1`. Run `pnpm run gen:types` in
`app/ui` after any schema change; `pnpm run check:types` fails if the file is stale.

## Tests
`uv run pytest backend/tests` (from `spine_monitor`). They run the app against an in-memory MQTT broker
and against the real engine over ASGI. Only the engine's feature extractor is replaced, because the
real one is not implemented yet.
