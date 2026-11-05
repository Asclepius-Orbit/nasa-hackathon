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
- **Engine calls** (`analysis.py`): when a session ends, `/features`. When it is pre-flight,
  `/baseline` with every pre-flight feature set (new version each time). Then `/assess` with profile,
  baseline, recent feature sets, self-reports and daily context. Every self-report also triggers
  `/assess`. A missing profile, baseline or feature set skips the assessment and says why. Absence is
  never turned into a green.
- **Pseudonymous ids**: `AST-01`-style ids only. Commands carry only the schema fields (which allow no
  others). The profile is personal data and never goes on the bus.

## UI API (`/ui`, `api.py`)
Write side for the UI. Separate from the read-only API of `contracts/app-openapi.yaml` used by the MCP
server. The app fills ids, `schema_version`, instants and `source`, then validates the entity against
its schema. Errors are `app/error` problem details.

| Method | Path | What |
|---|---|---|
| GET | `/ui/crew/{astronaut_id}/devices` | Latest status of each device |
| PUT/GET | `/ui/crew/{astronaut_id}/profile` | Astronaut profile |
| PUT/GET | `/ui/crew/{astronaut_id}/daily-context/{date}` | Daily context |
| POST | `/ui/crew/{astronaut_id}/self-reports` | Daily, weekly ODI or red flag; returns the report and the new assessment (or why it was skipped). Same `report_id` again is idempotent |
| GET | `/ui/crew/{astronaut_id}/self-reports/due` | Whether the daily report and the weekly ODI are due; red flags are always available |
| POST | `/ui/crew/{astronaut_id}/sessions` | Session plan (device, type, protocol, streams, load...) -> `start_session`. `mission_context` defaults to today's daily context |
| GET | `/ui/sessions/{session_id}` | Session, state (`starting`, `running`, `ended`, `processing`, `processed`, `processing_failed`, `rejected`) and feature set |
| POST | `/ui/sessions/{session_id}/events` | `mark_event` |
| POST | `/ui/sessions/{session_id}/calibrations` | `calibrate` |
| POST | `/ui/sessions/{session_id}/stop` | `stop_session` with a reason |
| GET | `/ui/commands/{command_id}` | Command state and acks (the UI polls it to move to the next step) |
| GET | `/health` | Liveness, MQTT connection and message counters |

## Running it
```
uv run uvicorn --factory monitor.main:create_app --port 8000
```
Settings are `SPINE_APP_*` variables (`settings.py`): `MQTT_HOST`, `MQTT_PORT`, `ENGINE_URL`, `DB_PATH`,
`ACK_TIMEOUT_S`, `ASSESS_WINDOW_DAYS`, `STORE_UNKNOWN_SESSION_CHUNKS`, `WEEKLY_ODI_INTERVAL_DAYS`. In
Docker Compose the database lives in the `backend_data` volume (`/data/spine_monitor.db`).

## TypeScript types for the UI
`app/ui/src/contracts/v1.ts` is generated from `contracts/schemas/v1`. Run `npm run gen:types` in
`app/ui` after any schema change; `npm run check:types` fails if the file is stale.

## Tests
`uv run pytest backend/tests` (from `spine_monitor`). They run the app against an in-memory MQTT broker
and against the real engine over ASGI. Only the engine's feature extractor is replaced, because the
real one is not implemented yet.
