**English** · [Español](README.es.md) · [Català](README.ca.md)

# spine-contracts (v1, draft for review)

Data contracts shared by every component: wearable gateway, MQTT broker, on-board app, engine and MCP server.
**The JSON Schemas are the source of truth.** Everything else is generated from them or checked against them.

## Layout
| Path | What |
|---|---|
| `schemas/v1/common.schema.json` | Shared definitions: ids, enums, quality, placement |
| `schemas/v1/telemetry/` | MQTT messages: stream chunk, device status, command, command ack, clock sync. The `domain/calibration` entity is also published on the bus (see Conventions) |
| `schemas/v1/domain/` | Entities: session, calibration, astronaut profile, self-report, daily context, feature set, baseline, assessment |
| `schemas/v1/engine/` | HTTP bodies of the engine: `/features`, `/baseline`, `/assess`, `/simulate`, errors |
| `schemas/v1/app/` | HTTP bodies of the read-only API of the app: status, history, explanation, lists of assessments, sessions and self-reports, errors; and the UI views of `ui-openapi.yaml` (`ui-*`) |
| `schemas/v1/agent/` | HTTP bodies of the LLM agent chat API (`agent-openapi.yaml`): the question (`chat-request`) and the server-sent events of the answer (`chat-event`) |
| `catalog/feature-catalog.json` | Every derived metric (feature) with unit, inputs, requirements, `risk_direction` and ranges. `catalog_version` 0.2.0: the directions and ranges are **provisional** (status stays `proposed`, every range source starts with "provisional, pendiente de Aguado") until Aguado validates them |
| `catalog/protocol-catalog.json` | Fixed facts of each protocol (band, sets, reps, rests, grip, tempo, posture, fit item ids) quoted by the UI views |
| `catalog/calibration-defaults.json` | Provisional defaults of the gateway calibrations (cord curve and rest point, EMG MVC window), the same values as the example mission; PENDING Aguado's measurement (#106) |
| `asyncapi.yaml` | MQTT topics, direction, QoS and retain (the gateway's point of view) |
| `openapi.yaml` | Engine HTTP API |
| `app-openapi.yaml` | Read-only HTTP API of the app (GET only), used by the MCP server |
| `ui-openapi.yaml` | View API of the app for the desktop UI (GET only): one screen per endpoint, codes and data, no prose |
| `agent-openapi.yaml` | Chat API of the LLM agent for the desktop app: POST a question, stream the answer as server-sent events |
| `examples/valid/` | One valid example per message; they are the mocks for everyone |
| `src/spine_contracts/` | Python package: generated `models/`, `messages` (unions), `topics`, `validation`, `openapi_drift` |

## What the system measures
- **EMG** (1 differential channel, mV, 1000 Hz): muscle activity, to compare Earth vs microgravity for the same standardised exercise.
- **IMU MPU-6050** (accel in g, gyro in deg/s, 100 Hz by default): movement of the back during the exercise.
- **Stretch cord** (ADC counts, ohm, derived length in mm, 20 Hz): elongation of the spine.
- **Self-report**: pain (NRS 0-10), location, stiffness, sleep, weekly Oswestry, red flags. The Oswestry is stored and shown (Oswestry screen, history) but does not score risk: the engine, the risk model and the rules ignore it. Whether it goes in the Houston summary is pending (#85); `ground-summary` has no ODI field for now.
- **Context**: mission phase, gravity environment, medication, exercise and countermeasures.
- **Profile**: age, sex at birth, anthropometry, spine and space history, fitness.

Data belongs to a **session**: `exercise` (standardised protocol, e.g. standing lat pulldown, declared load) or `spine_check` (short passive measurement). Only data of the same `protocol_id` is comparable, and Earth vs space is read through the `gravity_environment`.

### The standardised exercise protocol
`lat_pulldown_standing_v1` (same id, values fixed by Aguado in issue #12):
- **Exercise:** standing lat pulldown with an elastic band, pronated grip, moderate tempo. Green band (high resistance, 15-35 kg depending on the stretch; 25 kg nominal), 4 sets of 15 repetitions (60 in total, about 5 minutes) with very short recoveries (`rest_between_sets_s`, a nominal 30 s until Aguado confirms it). The session declares `planned_sets`, `reps_per_set` and `planned_reps` (the total, `planned_sets * reps_per_set`); events `set_start` / `set_end` (with `set_index`) delimit the sets, and `rep_index` counts repetitions over the whole session.
- **EMG:** one stream per latissimus dorsi (`emg_lat_left`, `emg_lat_right`), electrodes about 10 cm apart (`electrode_spacing_cm`) spanning T11-L2 (`span`), ground (GND) on the iliac crest (`ground.location`).
- **IMU:** `imu_t9`, at T9.
- **Stretch cord:** `stretch_spine`, region `full_spine`, spanning C3-C4 to L5-S1 (about 650 mm); its features are `stretch.full_spine.*`.
- **Spine checks:** `spine_length_rest_v1`, standing, just before (`phase: pre`) and just after (`phase: post`) the exercise; `related_session_id` is the exercise session they bracket.

## Conventions
- Field names and descriptions in English, `snake_case`.
- **Canonical units fixed per channel** (g, deg/s, mV, ohm, mm, cm, kg, Hz): nothing is converted on reception.
- **MQTT payloads are chunks**: one stream, consecutive samples, one array per channel, `seq` to detect loss. Streams have independent rates.
- **Time**: `t0_device_ms` (monotonic) + `boot_id` on the device; the gateway adds `received_at` (UTC) and publishes `clock-sync` anchors. Everything else is RFC 3339 with offset.
- **Quality** is a score 0..1 plus enumerated `flags` per channel and chunk, and says who assessed it.
- `source` is `device`, `simulator` or `replay`: a demo mixing real and simulated data must say so.
- **Calibrations are on the bus**: `asclepius/v1/crew/{astronaut_id}/device/{device_id}/calibration/{stream_id}` (QoS 1, retained, one topic per stream, payload = `domain/calibration`). The latest one in force stays retained, so a consumer can resolve the `calibration_id` of stretch chunks. `topics.calibration`, `topics.parse_calibration` and `SUBSCRIBE_ALL_CALIBRATIONS` cover it.
- Ids are pseudonymous (`AST-01`); no personal data travels over the MQTT bus.
- Every message carries `schema_version` (semver, major 1).

## Using it from Python
```python
from spine_contracts import stream_chunk_adapter, dump, topics, validation

chunk = stream_chunk_adapter.validate_json(payload)      # EmgChunk | ImuChunk | StretchChunk
topic = topics.stream("AST-01", "wb-01", chunk.stream_id)
validation.validate("telemetry/stream-chunk", dump(chunk))  # full rules, see below
```
- Pydantic models check structure, types and ranges. They do **not** enforce the `if/then` rules of the schemas (an exercise session needs `load_declared`, a derived cord length needs `calibration_id`, a required escalation needs an `urgency`). Call `validation.validate` where data enters the system.
- Serialise with `dump(model)`: only set fields are emitted, which is what the schemas expect.
- Regenerate the models after any schema change: `uv run python contracts/scripts/generate_models.py` (run from `spine_monitor`). A test fails if they are stale. Never edit `models/` by hand.
- Run the checks: `uv run pytest` (schemas, examples, mutations that must be rejected, generated code, topics, AsyncAPI/OpenAPI references).

## Read-only API of the app
`app-openapi.yaml` is what the MCP server talks to; it never touches the app's database. Only `GET`: no endpoint modifies data.
- **Per-tool endpoints** return the work already done for one MCP tool: `/crew/{astronaut_id}/status` (`get_current_status`), `/crew/{astronaut_id}/history?from&to` (`get_history`), `/assessments/{assessment_id}/explanation` (`explain_assessment`). `compare_periods` has no endpoint: the MCP server calls `/history` twice and compares.
- **Resource endpoints** return one entity or a list: assessments, sessions (metadata only, never raw chunks), the feature set of a session, self-reports and baselines.
- Lists need `from` and `to` (RFC 3339 with offset) and accept `limit` (1-500, default 100). They are ordered oldest first; when there are more items the most recent ones are kept and `truncated` is `true`.
- Missing data is `null`, and `status.freshness` marks it `stale: true`: absence is never presented as good news. The app decides the stale thresholds.
- Errors are RFC 9457 problem details (`app/error`), as in the engine.
- The app builds this API with FastAPI. The contract is the reference: compare its generated document with `openapi_drift.diff(contract, app.openapi())`, which lists routes and `operationId`s that were added, removed or renamed.
- `from` is a Python keyword: the generated models call it `from_` and keep `from` as alias. `dump` emits aliases.

## Changing a contract
Edit the schema, add or update its example, regenerate, run the tests, and tell the owners of the other layers. Backwards-compatible additions bump the minor version; anything else the major.

## Open
- **Aguado:** validate the feature catalog (ids, units, `risk_direction`, ranges and their sources): the `risk_direction` values and ranges in `catalog_version` 0.2.0 are provisional and pending Aguado (#38; the left-right symmetry index, #106, is left untouched); the reduced ODI item set; the recovery between sets (30 s is a placeholder), the MVC procedure and the exact band stretch; the measurement of the stretch cord (about 650 mm, PENDIENTE medición Aguado: the resistance-to-length curve is a placeholder until it is measured with an ohmmeter; it is non-linear and recovers slowly, so the length comes from a measured curve).
- **Polfer:** does the EMG module output the raw signal or an envelope (spectral features need raw)? Use ADC1 pins on the ESP32 (ADC2 does not work with Bluetooth/WiFi). The wearable-to-gateway format is free; the contract starts at MQTT. The real gateway uses the serial protocol documented in `spine_monitor/gateway/README.md` and `firmware/README.md`.
- **IMU rate:** 100 Hz by default; 10 Hz is too low for smoothness and peak velocity of a ~2 s repetition.
- MCP tool outputs: `compare_periods` has its own schema, `mcp/compare-periods` (computed by the server from two `/history` calls); the other MCP tools reuse the `app` and `engine` schemas.
- TypeScript types for the UI are generated from these schemas into `app/ui/src/contracts/v1.ts` (`pnpm run gen:types` in `app/ui`; `pnpm run check:types` fails when they are stale). Regenerate them after any schema change.
- Not covered yet: the report to Houston (FHIR/DTN).
- Review: Andreea. Semantic validation: Aguado.

## Known limitations
- In microgravity the accelerometer does not see gravity: IMU features that rely on orientation are not comparable with Earth; the catalog prefers gyroscope-based ones.
- The Python `jsonschema` library matches `pattern` with `re.search`, where `$` accepts a trailing newline; `topics` uses `fullmatch` for that reason.
- Generated models use `constr(...)` types so that constrained list items stay plain strings; static type checkers may flag them.
