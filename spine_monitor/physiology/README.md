**English** · [Español](README.es.md) · [Català](README.ca.md)

# spine-physiology

Synthetic mission generator. It publishes what the real hardware would: `StreamChunk` v1 with `source: simulator` (EMG, IMU and stretch cord), sessions, calibrations, device status, clock sync, daily self-reports, daily context, astronaut profiles and, on top of that, the **ground truth** (what really happened) so the engine can be evaluated against the simulator.

## Design

- **Pure core.** `MissionGenerator` is a lazy iterator of `Envelope`s (a contract message plus its topic, retained flag and UTC instant `at`). It does no I/O.
- **Sinks.** `JsonlSink` writes everything to disk; `MqttSink` publishes only the messages that have a topic (chunks, device status, clock sync, command acks). The fake gateway reuses the same core in real time.
- **Contracts.** Payloads are built with `spine_contracts` models and validated against its JSON Schemas; `source` is always `simulator`.

## No clinical values in the code

Every numeric parameter, physiological or technical, is mandatory and comes from the mission YAML: the code has no defaults. Optional contract fields are written as an explicit `null`. `configs/example-mission.yaml` is a **PLACEHOLDER**: invented numbers so the simulator runs end to end, not clinical references. Real values are meant to replace it. Quote times of day (`"07:00"`) and write exponents as `1.0e-3` (PyYAML reads `1e-3` as text); the loader is strict and fails instead of converting silently.

## Configuration

`load_config(path)` validates the YAML with Pydantic (unknown keys are rejected). Blocks of `configs/example-mission.yaml`:

| Block | What it sets |
|---|---|
| `seed`, `start_date`, `start_mission_day` | master seed; date of day 0; mission day of day 0 (launch = 0) |
| `segments` | consecutive stretches (`days`, `phase`, `gravity_environment`): preflight 1 g, inflight microgravity, postflight 1 g |
| `crew` | per astronaut: ids, `latent_multipliers`, `exercise_adherence`, baseline (cord rest length, EMG MVC per stream) and the `AstronautProfile` |
| `device` | firmware, link, daily power on/off (UTC), battery, temperature, status and clock-sync intervals, streams with their placement |
| `protocols` | exercise protocols (reps, durations, %MVC, ROM, load) and spine-check protocols |
| `schedule` | sessions per day (`time_utc`, jitter, phases, `every_n_days`) and the optional background monitoring window |
| `calibration` | stretch, EMG MVC and IMU calibrations: periodicity, noise, points |
| `curves` | typical trajectory of the four daily latents by mission day (piecewise points), `daily_sd` and `dose_drift_per_day` |
| `signals` | `chunk_ms` and the synthesis of EMG, IMU and stretch (rates, noise, filters, ADC, hysteresis...) |
| `faults` | `quality` (one entry per quality flag) and `device` (packet loss, reboots, clock) |
| `daily_context` | planned exercise, countermeasures and medication per phase |
| `self_report` | time, NRS model for pain and stiffness, pain locations, sleep, red flags |

The four daily latents are `spine_elongation` (mm over the preflight rest length), `muscle_deconditioning`, `rom_motor_control` and `pain_stiffness` (0 = preflight, 1 = maximum modelled deterioration). Daily value = astronaut multiplier x curve(mission day) + dose drift + N(0, `daily_sd`). The curves are the typical trajectory *with* the planned exercise; each day the drift adds `dose_drift_per_day x (1 - actual minutes / planned minutes)`.

## Quick start

From the `spine_monitor` workspace. Day index `d` is the date `start_date + d`; the example mission has 14 days (0 to 13).

```python
from pathlib import Path
from spine_physiology import MissionGenerator, load_config
from spine_physiology.sinks.jsonl import write_jsonl

config = load_config("physiology/configs/example-mission.yaml")
gen = MissionGenerator(config)                 # seed from the YAML; MissionGenerator(config, seed=7) overrides it

# Everything of one astronaut on one day, regenerated alone (same payloads as in the full run)
for env in gen.day_messages(5, "AST-01"):
    print(env.kind.value, env.topic, env.at)  # kind, MQTT topic or None, UTC instant
    break

# To disk: one folder per astronaut and day (the full mission is gen.iter_messages())
n = write_jsonl(gen.day_messages(5, "AST-01"), Path("out"), config=config, seed=gen.seed)
```

Publishing to MQTT (any object with the `aiomqtt.Client.publish` signature works; QoS 1, retained only for device status). Domain objects are skipped and not counted:

```python
import asyncio
import aiomqtt
from spine_physiology.sinks.mqtt import MqttSink, publish_all

async def main() -> None:
    async with aiomqtt.Client("127.0.0.1", 1883) as client:
        sent = await publish_all(gen.day_messages(5, "AST-01"), MqttSink(client))
        print(sent, "messages published")

asyncio.run(main())
```

`spine_physiology.sinks.pacing.paced` can pace a stream at real time or `speed` x real time. A single day of the example mission is about 83 000 messages, almost all background chunks (see Known limits); use `gen.session_messages(ref)` for a cheaper slice (below).

## Reproducibility

- Same config and same seed give the same bytes: JSONL has compact JSON, fixed key order, UTF-8 and no wall-clock values.
- Randomness comes from independent streams derived per branch (astronaut, day, session, stream...), so changing one branch does not shift the rest.
- The device starts clean every day, so nothing carries from one day to the next except the dose drift of the latents, which can be regenerated per day.
- `gen.day_messages(day, astronaut_id)` and `gen.session_messages(ref)` give exactly the same payloads, in the same relative order, as inside the full `iter_messages()` run.

## Earth vs microgravity (`resimulate`)

The same session, regenerated with another gravity, gives an exact A/B pair (same seed, same schedule, same faults; only the physics changes):

```python
from spine_contracts import GravityEnvironment
from spine_physiology import SessionRef

ref = SessionRef("AST-01", 5, "lat_pulldown")        # astronaut, day, slot = `name` in the schedule
micro = list(gen.session_messages(ref))              # as configured (microgravity on inflight days)
earth = gen.resimulate(ref, GravityEnvironment.EARTH_1G)
```

`gen.sessions(day, astronaut_id)` lists the `SessionPlan`s of a day (`plan.ref`, `session_id`, start and end).

## Faults

**Quality faults** (`faults.quality`, one entry per `QualityFlag`). Each has a probability per chunk of starting an episode, a duration range, a quality `score`, an `amplitude` (relative to the clean signal of the channel in that chunk: RMS, or peak for `clipping`) and the stream `kinds` it affects:

| Flag | What it does to the signal |
|---|---|
| `clipping` | clips to +-amplitude x peak |
| `lead_off` | replaces the signal with noise of amplitude x RMS |
| `motion_artifact` | adds a low-frequency burst (< 20 Hz) |
| `powerline_noise` | adds a sine at `frequency_hz` |
| `low_snr` | adds white noise |
| `saturated_adc` | counts pinned at the ADC maximum (or the channel range) |
| `out_of_calibration` | adds an offset |
| `sensor_warmup` | adds a slow drift that decays |

On the stretch cord only `adc_counts` is altered (integers, clipped to the ADC); `length_mm` is derived from them and inherits their quality. Each affected chunk carries the flag and the minimum score.

**Device faults** (`faults.device`):

- `packet_loss`: lost chunks in bursts. They also consume `seq`, so the gap is visible; the first chunk after a loss carries `packet_gap`, and long losses mark the stream as stalled in the device status.
- `reboot`: spontaneous reboots (Poisson per hour) with offline time; after one, `seq` and `t0_device_ms` restart at 0 with a new `boot_id`.
- `clock`: per-boot clock drift (ppm) and reception latency; when the error since the last sync passes the threshold, chunks carry `clock_uncertain`.

The ground truth records every injected episode, every lost chunk and every reboot.

## Output layout

```
out/
  manifest.json                    config, seed, package and contract versions
  AST-01/
    profile.json                   AstronautProfile
    day-012/
      chunks.jsonl  sessions.jsonl  calibrations.jsonl  self_reports.jsonl
      daily_context.jsonl  device_status.jsonl  clock_sync.jsonl
      command_acks.jsonl  ground_truth.jsonl
```

Each line is `{"topic", "retained", "at", "payload"}` for message types with a topic (chunks, status, clock, acks) and `{"at", "payload"}` for domain objects. `JsonlSink` opens files lazily with a cap on open handles and writes `manifest.json` on `close()` (it also works as a context manager).

`ground_truth.jsonl` has no contract schema; each record has `type`, `astronaut_id` and `day` (session records also `session_id` and `slot`): `latent_state` (daily latents and the dose that produced them), `device_events` (boots, reboots, drift), `quality_faults`, `lost_chunks` and `expected_features` (true values of the feature catalog computed from the model without sensor noise or faults).

## Fake gateway

`spine-sim-gateway` behaves on MQTT like the real wearable gateway, one device per astronaut of the config. Settings are environment variables (or a `.env`):

| Variable | Default | Meaning |
|---|---|---|
| `SPINE_SIM_CONFIG` | required | mission YAML |
| `SPINE_SIM_SEED` | the YAML's | overrides the seed |
| `SPINE_SIM_MQTT_HOST` / `SPINE_SIM_MQTT_PORT` | `127.0.0.1` / `1883` | broker |
| `SPINE_SIM_MQTT_CLIENT_ID` | `spine-sim-gateway` | client id prefix; each device connects as `<prefix>-<device_id>` |
| `SPINE_SIM_SPEED` | required | mission-day speed: 1 = real time, 0 = fixed day |
| `SPINE_SIM_START_DAY` | required | mission day index at startup (0 = first simulated day) |

```bash
SPINE_SIM_CONFIG=physiology/configs/example-mission.yaml SPINE_SIM_SPEED=1 SPINE_SIM_START_DAY=0 \
  uv run spine-sim-gateway
```

- It subscribes to each device's command topic and answers on the ack topic. Commands are validated against the JSON Schema. Supported: `start_session`, `stop_session`, `mark_event` and `calibrate`. Every accepted command gets `accepted` and then `completed`; `start_session` is `rejected` with `busy`, `unknown_stream` or `device_offline`, and `stop_session` and `mark_event` with `no_session` when there is nothing to stop or mark. `mark_event` does not change the signal; `calibrate` returns a `calibration_id`.
- Retained `DeviceStatus` and `ClockSync` at startup and periodically, a Last Will with `status: offline`, and the same `offline` on clean shutdown. That is why there is **one MQTT connection per device**: MQTT allows a single Last Will per connection.
- If `schedule.background.enabled`, the IMU and stretch background streams run whenever there is no session (the daily time window of the config is ignored, so demos work at any hour). A session pauses the background (same `stream_id`, `seq` continues) and it resumes with the same `session_id` afterwards.
- Mission day = `start_day` + elapsed wall-clock days x `speed`, capped to the mission. A session never changes day halfway.
- When the battery of the daily power-on window runs out and there is no session, the device "recharges": new `boot_id`, `seq` and clock at 0, full battery.

## Docker

The `simulator` service is in the `sim` profile of `app/docker-compose.yml` (it needs the broker, which starts with it). From the repo root:

```bash
docker compose -f app/docker-compose.yml --profile sim up --build
```

It runs `spine-sim-gateway` with the example mission (`SPINE_SIM_CONFIG=/app/configs/example-mission.yaml`), speed 1 and start day 0. The image is built with `spine_monitor/physiology/Dockerfile` (context: the repo root) and runs as a non-root user.

## Tests

```bash
uv run pytest physiology
```

Tests that need a real broker are marked `mosquitto` and are skipped when nothing listens on `127.0.0.1:1883` (for example `docker run -p 1883:1883 eclipse-mosquitto:2 mosquitto -c /mosquitto-no-auth.conf` enables them). The gateway tests otherwise use a fake MQTT client and virtual time.

## Known limits

- Background volume: chunks are 100 ms, so every background hour is about 36 000 chunks per stream. The example config limits the background to one hour a day for that reason.
- The weekly ODI questionnaire is deferred: only the daily self-report is generated.
- Calibrations, sessions, self-reports, daily context, profiles and ground truth have no MQTT topic: they only exist in JSONL (domain objects never go to the bus).
- Every number in the example config is a placeholder, not a clinical reference.
