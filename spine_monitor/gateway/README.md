**English** · [Español](README.es.md) · [Català](README.ca.md)

# spine-gateway

Real wearable gateway. It reads the Arduino Uno/Nano of the wearable over USB serial and publishes on the MQTT v1 bus what the contract defines: stream chunks, device status, clock sync, calibrations and command acks. Its observable behaviour is the same as the fake gateway of `spine_monitor/physiology`, so the backend, the engine and the UI cannot tell them apart (apart from `source: device` instead of `simulator`).

## Verification status

Verified **without hardware**:

- Unit tests of every module (protocol, assembler, calibration, commands, status, serial source, app, emulator, settings) and property tests of the protocol and the assembler.
- Emulator of the Arduino (`spine-arduino-emulator`) speaking the serial protocol over TCP.
- End-to-end test against a real Mosquitto: emulator -> gateway -> broker -> observer, with full schema validation, topics, QoS, retained and acks.
- Build of the firmware (`firmware-build`): parity test of `protocol.h` against the golden vectors and compilation for `arduino:avr:uno`.

**Not tested yet with a real board.** Nothing of this has run on hardware:

- Real timing of the ADC sampling and the Timer1 ISR (1000 Hz EMG, 20 Hz cord).
- I2C reading of the MPU-6050 (100 Hz, 400 kHz).
- Throughput of the USB-serial bridge at 250000 baud (about 5 KB/s of average payload).
- The real EMG module: power supply, range of `SIG`, and whether it outputs a raw signal or an envelope.
- Noise of the stretch cord voltage divider.
- Reset of the Uno when the serial port is opened (the gateway assumes it and waits for the next hello/tick).
- Passing the USB device to Docker on Linux (`gateway` service, profile `hardware`).
- SIGTERM handling of the emulator in Docker.

## Architecture

Package `spine_gateway` (`src/spine_gateway/`):

| Module | What it does |
|---|---|
| `settings.py` | `GatewaySettings`: every `SPINE_GATEWAY_*` variable (or `.env`) with validation of ids against `spine_contracts.topics`. |
| `protocol.py` | Serial line protocol: parser (`parse_line`, never raises) and encoders, CRC-8/MAXIM, `frame_gap` on the `u16` counter. |
| `ports.py` | Port `LineSource` and its events (`SerialLine`, `LinkUp`, `LinkDown`). |
| `serial_source.py` | `SerialLineSource`: pyserial in a worker thread, splits by `\n`, reconnects with exponential backoff. |
| `assembler.py` | `ChunkAssembler`: turns `D` frames into `EmgChunk`/`ImuChunk`/`StretchChunk` (units, quality, `seq`, gaps, 10 Hz background windows). |
| `calibration.py` | `CalibrationRecorder`: records `stretch` and `emg_mvc` calibrations from the frames. |
| `commands.py` | Reads and validates `cmd` payloads, builds `command-ack` messages. |
| `status.py` | `DeviceStatus`, `ClockSync` and the background `session_id`; stream and global state. |
| `app.py` | `Gateway`: MQTT loop (aiomqtt), Last Will, timers, commands, sessions and calibrations. |
| `emulator.py` | Fake Arduino over TCP (`spine-arduino-emulator`) for working without a board. |
| `__main__.py` | Entry point `spine-gateway`. |

Data flow:

```
Arduino (firmware/spine_wearable) --USB serial, 250000 baud--> lines H / T / D (+ CRC-8)
  -> SerialLineSource -> Gateway (parse_line, ChunkAssembler, CalibrationRecorder)
  -> MQTT v1 (aiomqtt, QoS 1) -> Mosquitto
```

## Running it

The commands below start from the repository root; `uv` commands run from `spine_monitor/`.

### Native, with the real Arduino

Start the broker (it only listens on `127.0.0.1:1883`) and the gateway:

```bash
docker compose -f app/docker-compose.yml up -d mosquitto
cd spine_monitor
SPINE_GATEWAY_SERIAL_URL=/dev/ttyACM0 uv run spine-gateway      # Linux
```

```powershell
cd spine_monitor
$env:SPINE_GATEWAY_SERIAL_URL = "COM3"; uv run spine-gateway    # Windows
```

The broker is expected at `127.0.0.1:1883` (`SPINE_GATEWAY_MQTT_HOST` / `SPINE_GATEWAY_MQTT_PORT`). Variables can also go in a `.env` file in the directory where the gateway is started. `Ctrl+C` (or SIGTERM) leaves a retained `offline` status and closes the port and the connection.

### Without hardware: emulator

Two terminals, with the broker up:

```bash
cd spine_monitor
uv run spine-arduino-emulator --port 5000
SPINE_GATEWAY_SERIAL_URL=socket://127.0.0.1:5000 uv run spine-gateway
```

pyserial turns `socket://host:port` into a TCP connection, so the gateway runs the exact same code as with a board. The emulator produces deterministic synthetic signals and gives each client a fresh device (new `boot_id`, counters at zero), like an Arduino reset by opening the port. Options: `--host` (default `127.0.0.1`), `--port` (default 5000), `--seed`, `--no-imu` (firmware without MPU-6050: no `I` frames and `#imu absent`), `--drop-every N` (every Nth batch is not sent but consumes its `frame_seq`, like an overrun) and `--reboot-after S` (device reboot after S seconds).

The same demo in Docker, profile `gateway-demo` (emulator + gateway, Mosquitto starts as a dependency). The image `spine-gateway:dev` is built on the first `up` (use `--build` after changing the code):

```bash
docker compose -f app/docker-compose.yml --profile gateway-demo up -d arduino-emulator gateway-demo
```

### Real board in Docker (Linux only)

Profile `hardware`, service `gateway`:

```bash
docker compose -f app/docker-compose.yml --profile hardware up -d gateway
```

The device is mapped to `/dev/ttyACM0` inside the container; `SPINE_GATEWAY_SERIAL_DEVICE` chooses the host device (default `/dev/ttyACM0`). It only works on Linux: Docker Desktop on Windows and macOS cannot pass a USB device to a container, so there the gateway runs natively.

### Notes

- The local broker has no authentication and only listens on `127.0.0.1`. Exposing it on the network requires authentication first (issue #96, out of scope here).
- Observe the bus: `docker compose -f app/docker-compose.yml exec mosquitto mosquitto_sub -h 127.0.0.1 -t 'asclepius/v1/crew/AST-01/device/wb-01/#' -v` (ids of the default configuration).

## Configuration

All variables have the prefix `SPINE_GATEWAY_` and are optional; `.env.example` lists them commented. Ids are validated with the same expressions `spine_contracts.topics` uses to build topics.

| Variable | Default | What it does |
|---|---|---|
| `SPINE_GATEWAY_SERIAL_URL` | `COM3` | Serial port as pyserial understands it: `COM3`, `/dev/ttyACM0` or `socket://host:port`. |
| `SPINE_GATEWAY_SERIAL_BAUD` | `250000` | Baud rate (the firmware is fixed at 250000). |
| `SPINE_GATEWAY_SERIAL_RECONNECT_INITIAL_S` | `1.0` | First wait (s) before reopening the port if it fails to open or is unplugged. |
| `SPINE_GATEWAY_SERIAL_RECONNECT_FACTOR` | `2.0` | Multiplier of the wait after each failed attempt (>= 1). |
| `SPINE_GATEWAY_SERIAL_RECONNECT_MAX_S` | `10.0` | Cap (s) of the serial reconnection wait (>= initial). |
| `SPINE_GATEWAY_ASTRONAUT_ID` | `AST-01` | Astronaut the data is attributed to (goes in the topics). |
| `SPINE_GATEWAY_DEVICE_ID` | `wb-01` | Device id (goes in the topics and in the MQTT client id). |
| `SPINE_GATEWAY_MQTT_HOST` | `127.0.0.1` | MQTT broker host. |
| `SPINE_GATEWAY_MQTT_PORT` | `1883` | MQTT broker port (1-65535). |
| `SPINE_GATEWAY_MQTT_CLIENT_ID` | `spine-gateway` | MQTT client id prefix; the connection uses `<client_id>-<device_id>`. |
| `SPINE_GATEWAY_MQTT_RECONNECT_INITIAL_S` | `1.0` | First wait (s) before reconnecting to the broker. |
| `SPINE_GATEWAY_MQTT_RECONNECT_FACTOR` | `2.0` | Multiplier of the wait after each failed attempt (>= 1); returns to the initial wait after connecting. |
| `SPINE_GATEWAY_MQTT_RECONNECT_MAX_S` | `30.0` | Cap (s) of the MQTT reconnection wait (>= initial). |
| `SPINE_GATEWAY_STATUS_INTERVAL_S` | `30.0` | Period (s) of the `status` message (it is also published as soon as something relevant changes). |
| `SPINE_GATEWAY_CLOCK_SYNC_INTERVAL_S` | `60.0` | Period (s) of the `clock` message. |
| `SPINE_GATEWAY_CLOCK_UNCERTAINTY_MS` | `20.0` | Estimated error (ms) of the clock anchor: tick arrival time at the gateway (USB latency + line reading). |
| `SPINE_GATEWAY_STALL_TIMEOUT_S` | `2.0` | A stream without batches for this long (s) becomes `stalled` and the device `degraded`. |
| `SPINE_GATEWAY_CALIBRATION_DEFAULT_DURATION_S` | `5.0` | Duration (s) of a `calibrate` command without `duration_s`. |
| `SPINE_GATEWAY_EMG_STREAM_ID` | `emg_lat_left` | `stream_id` of the EMG. |
| `SPINE_GATEWAY_IMU_STREAM_ID` | `imu_l3` | `stream_id` of the IMU. |
| `SPINE_GATEWAY_STRETCH_STREAM_ID` | `stretch_lumbar` | `stream_id` of the stretch cord. |
| `SPINE_GATEWAY_EMG_MUSCLE` | `latissimus_dorsi` | Muscle where the EMG electrode is placed (contract `Muscle` enum); goes in the `emg_mvc` calibration. |
| `SPINE_GATEWAY_EMG_SIDE` | `left` | Side of the electrode (`left`, `right` or `midline`). |
| `SPINE_GATEWAY_ADC_BITS` | `10` | ADC resolution (the ATmega328P has 10 bits). |
| `SPINE_GATEWAY_VREF_V` | `5.0` | ADC reference voltage (V). |
| `SPINE_GATEWAY_EMG_INPUT_PIN` | `A0` | EMG input pin, declared in the chunk (`acquisition.input_pin`). |
| `SPINE_GATEWAY_STRETCH_INPUT_PIN` | `A1` | Cord input pin, declared in the chunk (`circuit.input_pin`). |
| `SPINE_GATEWAY_STRETCH_SERIES_RESISTOR_OHM` | `10000.0` | Fixed resistor of the cord divider (ohm); used to compute `resistance_ohm`. |
| `SPINE_GATEWAY_IMU_ACCEL_RANGE_G` | `4` | Accelerometer range (g) used to convert counts: 2, 4, 8 or 16. Must match the firmware (fixed at 4). |
| `SPINE_GATEWAY_IMU_GYRO_RANGE_DPS` | `500` | Gyroscope range (deg/s): 250, 500, 1000 or 2000. Must match the firmware (fixed at 500). |
| `SPINE_GATEWAY_IMU_DLPF_HZ` | `44` | Digital low-pass filter of the MPU-6050, declared in the chunk (`config.dlpf_hz`). The firmware configures 44 Hz. |
| `SPINE_GATEWAY_EMG_GAIN` | `1.0` | Gain of the EMG module; mV are divided by it. |
| `SPINE_GATEWAY_EMG_SIGNAL_KIND` | `envelope` | What the EMG module delivers to the ADC: `raw`, `rectified` or `envelope`. The default is an assumption, not confirmed with the board (see below). |
| `SPINE_GATEWAY_STRETCH_REST_LENGTH_MM` | `100.0` | Length (mm) of the cord at rest, used by the `stretch` calibration. Placeholder until measured (#12). |
| `SPINE_GATEWAY_EMG_MVC_WINDOW_MS` | `250` | Window (ms) of the sliding RMS of the `emg_mvc` calibration. |

`SPINE_GATEWAY_EMG_SIGNAL_KIND` sets `signal_kind` in the EMG chunks and in the `emg_mvc` calibration, and decides how mV and quality are computed:

- `envelope` (default) and `rectified`: unipolar signal. mV = `counts / 1023 * vref * 1000 / gain`. `clipping` only at the ADC maximum (0 is a muscle at rest, a healthy value).
- `raw`: bipolar signal assumed to be biased at mid-scale (`(2^bits - 1) / 2`, 511.5 counts). The gateway subtracts that offset before converting to mV, and the MVC RMS is computed on the signal with the offset subtracted. `clipping` at 0 and at the maximum. The bias at mid-scale is an unverified assumption.

## MQTT behaviour

Topics are always built with `spine_contracts.topics` (root `asclepius/v1/crew/{astronaut_id}/device/{device_id}/...`). Every message is QoS 1. One device per process.

| Topic | Direction | Retained | Content |
|---|---|---|---|
| `.../stream/{stream_id}` | gateway -> bus | no | `StreamChunk` (EMG, IMU, stretch). |
| `.../status` | gateway -> bus | yes | `DeviceStatus`. |
| `.../clock` | gateway -> bus | yes | `ClockSync`. |
| `.../calibration/{stream_id}` | gateway -> bus | yes | Calibration in force for the stream. |
| `.../ack` | gateway -> bus | no | `CommandAck`. |
| `.../cmd` | bus -> gateway | no | `Command` (the gateway subscribes). |

Nothing of the device is published until the first hello or tick arrives after opening the port (opening it resets the Uno).

### Status

`status` is published retained every `status_interval_s`, right after each MQTT connection and as soon as something relevant changes (global state, new `boot_id`, a stream stalls or recovers, rate change). It carries `source: device`, `boot_id`, `uptime_ms`, `firmware_version` (from the hello), `link: usb_serial` and one entry per stream with `state`, `sample_rate_hz` and `last_seq`. No battery or temperature: the Arduino does not measure them.

- Global state: `offline` with the serial link down or without any hello/tick since the port was opened; `degraded` if some stream is `stalled`; otherwise `ok`.
- Stream state: `active` (a batch arrived less than `stall_timeout_s` ago), `stalled` (it participates and has been `stall_timeout_s` without batches, or never sent any after that grace time; for instance the firmware sends no `I` frames when the MPU-6050 does not answer) and `disabled` (it does not participate, i.e. EMG outside a session that includes it, or no batch yet and the grace time has not passed).
- Last Will: `offline` retained, QoS 1, registered on connection. A clean stop (SIGTERM, Ctrl+C) publishes `offline` retained explicitly, because the Last Will only fires when the connection is lost.

### Clock sync

`clock` carries `method: gateway_receive_time`: the `millis()` of the last device tick and the UTC time at which it reached the gateway, with `uncertainty_ms = SPINE_GATEWAY_CLOCK_UNCERTAINTY_MS`. It is published after each MQTT connection, every `clock_sync_interval_s`, when a session starts, and on the first tick after a new `boot_id`.

### Chunks

- **Session** (between `start_session` and its end): one chunk per 100 ms batch, at native rate: EMG 1000 Hz (100 samples), IMU 100 Hz (10), cord 20 Hz (2). If `stream_ids` is omitted all three participate. A stream that is not in the session keeps going as background.
- **Background** (outside a session, IMU and cord only): the mean of each batch, 10 consecutive batches per chunk, 1 s chunks at 10 Hz. The EMG is not published in background. Its `session_id` is a deterministic uuid5 of the device and the UTC day (it changes at 00:00 UTC).
- **`seq`**: per `boot_id` and stream, starts at 0 and is shared by session and background. A new `boot_id` restarts it.
- **Gaps**: each lost batch consumes a `seq`, the next session chunk declares `dropped_samples` (lost batches x samples per batch), carries the flag `packet_gap` and a quality score of at most 0.7. In background a gap discards the partial window, consumes the `seq` of the 1 s windows it touches and flags `packet_gap` (no `dropped_samples`). Duplicate and out-of-order batches, and batches with an unexpected number of samples, are discarded.
- **Units**: EMG in mV at the ADC input (see `EMG_SIGNAL_KIND`); IMU in g and deg/s with the nominal sensitivities of the MPU-6050 for the configured range; cord in `adc_counts` plus `resistance_ohm = Rs * counts / (max - counts)`. If any sample of a cord chunk is at 0 or at the maximum (short or open circuit) the chunk omits `resistance_ohm` and `length_mm`. `length_mm` and `calibration_id` appear only while a `stretch` calibration is in force (piecewise linear curve, clamped at its ends with the flag `out_of_calibration`).
- **Quality** (`assessed_by: gateway`): `score` = samples without problem / samples. Flags: `clipping` (EMG at the ADC maximum, and at 0 for `raw`; cord at 0 or the maximum; IMU at -32768 or 32767 on some axis), `lead_off` (cord at the maximum: open circuit), `packet_gap` and `out_of_calibration`.

### Commands

The gateway subscribes to `.../cmd`. Types: `start_session`, `stop_session`, `mark_event` and `calibrate`. Every command gets acks on `.../ack`:

| Command | Acks, in order |
|---|---|
| `start_session` | `accepted`; later `completed` when the session ends (`stop_session` or `max_duration_s`). |
| `stop_session` | `accepted`; `completed` of the `start_session` command; `completed` of the `stop_session`. |
| `mark_event` | `accepted`, `completed`. No effect on the device. |
| `calibrate` | `accepted`; the calibration is published retained; `completed` with `result.calibration_id` (the first stream's), or `failed`. |

Rejections (`rejected`, `error.code`):

| Code | When |
|---|---|
| `busy` | `start_session` with a session running; `calibrate` with another calibration running. |
| `unknown_stream` | A `stream_id` that does not exist; in `calibrate`, a stream of another kind (`stretch` needs the cord stream, `emg_mvc` the EMG stream). |
| `no_session` | `stop_session` or `mark_event` for a session that is not the running one. |
| `device_offline` | `start_session` or `calibrate` while the device is not connected. |
| `invalid_command` | Invalid payload (JSON Schema), ids that do not match the topic, a duration over 30 days, a payload over 16 KB, or `calibrate` of type `imu`. |
| `internal` | Unexpected error (the ack is `failed`). |

Other rules:

- A payload without a readable `command_id` (UUID) cannot be answered and is ignored. A payload over 16 KB (16384 bytes) is not parsed: if its `command_id` can be found it is rejected with `invalid_command`.
- **Deduplication:** the last 256 `command_id`s are remembered. A QoS 1 redelivery receives the same acks again and is not executed twice.
- `calibrate` supports `stretch` and `emg_mvc`; `imu` is rejected with `invalid_command` (the enum of `error.code` has no "not supported"). It records for `duration_s` (default `SPINE_GATEWAY_CALIBRATION_DEFAULT_DURATION_S`) of **device time**; it does not require `session_id` to be the running session. If the data is not complete 5 s after `duration_s`, it fails.
- `calibration_failed` and `calibration_timeout` travel both as `error.code = "calibration_failed"`; the detail goes in the message: `<reason>: <detail>`, where the reason is `incomplete`, `rest_resistance_not_computable`, `too_few_samples`, `flat_signal` or `calibration_timeout`. Messages are cut at 300 characters.
- Calibrations live in the gateway memory and are republished retained after each MQTT reconnection, before the next chunk that cites them. A gateway restart starts without calibration.
- `stretch`: `rest_resistance_ohm` is the resistance computed from the mean of the counts; `rest_length_mm` comes from the configuration; the curve is the **default approximation**, a linear `R = R0 * L / L0` with points at `L0 * {1.0, 1.25, 1.5, 1.75, 2.0}`. It is not measured: the cord is non-linear and has hysteresis, and the measured curve of #12 must replace it.
- `emg_mvc`: `mvc_rms_mv` is the maximum of the sliding RMS (`SPINE_GATEWAY_EMG_MVC_WINDOW_MS`) in mV.

## Serial protocol

ASCII lines ending in `\n`, each `<body>*<XX>` with `XX` the CRC-8/MAXIM of the body in 2 uppercase hex digits. Base64 is standard with padding. Lines longer than 1024 characters are discarded.

| Line | Content |
|---|---|
| `H,1,<boot_id>,<fw_version>,<millis>` | Hello after boot (`1` = protocol version). |
| `T,<boot_id>,<millis>` | Tick every 1000 ms, anchor of the device clock. |
| `D,E,<frame_seq>,<t0_ms>,100,<b64>` | EMG: 100 x `uint16` LE, 1000 Hz, every 100 ms. |
| `D,I,<frame_seq>,<t0_ms>,10,<b64>` | IMU: 10 x (ax, ay, az, gx, gy, gz) `int16` LE, 100 Hz. |
| `D,S,<frame_seq>,<t0_ms>,2,<b64>` | Cord: 2 x `uint16` LE, 20 Hz. |
| `# ...` | Debug text of the firmware. It has no CRC, so the gateway discards it as a bad line (reason `no_crc`); a few warnings in the log after boot (`#imu ok`, ...) are expected. |

Lines that are not valid (bad CRC, bad field, bad base64...) are counted by reason, logged at 1, 2, 4, 8... occurrences (the rest at debug level) and discarded. The full detail (wiring, behaviours of the firmware, examples) is in [`firmware/README.md`](../../firmware/README.md).

## Tests

From `spine_monitor/`:

```bash
uv run pytest gateway/tests -q -p no:randomly                  # everything; broker tests are skipped without a broker
uv run pytest gateway/tests -q -p no:randomly -m mosquitto     # only the ones against a real broker
```

The `mosquitto` tests and the emulator E2E (`test_e2e_emulator_mosquitto.py`) need a broker on `127.0.0.1:1883` (`docker compose -f app/docker-compose.yml up -d mosquitto`); without it they are skipped. With `SPINE_REQUIRE_BROKER=1` they fail instead of skipping, which is how the gate runs them. They use their own device ids (`itest-*`) and delete the retained messages they leave.

Quality gate (ruff, pyright, tests with coverage and incremental mutation), the same locally and in CI:

```bash
GATE_MUTATION=changed docker compose -f app/docker-compose.yml run --rm quality
```

The `gateway` package is new in the workspace, so the `spine-quality` image has to be rebuilt once before the first run: `docker compose -f app/docker-compose.yml build quality`.

Firmware (parity test of the protocol against the golden vectors plus compilation): `docker compose -f app/docker-compose.yml --profile firmware run --rm firmware-build`.

## Wiring and test with the real board

For when the Arduino is available. Nothing in this section has been tried on hardware.

### Pins

| Signal | Arduino pin | Notes |
|---|---|---|
| EMG module `SIG` | `A0` | See the power supply warnings below. |
| Stretch cord | `A1` | Divider: 5 V -> 10 kohm resistor -> middle point (`A1`) -> cord -> GND. |
| MPU-6050 `SDA` | `A4` | I2C, address 0x68. |
| MPU-6050 `SCL` | `A5` | |
| MPU-6050 `VCC` / `GND` | 5 V (GY-521) / GND | |
| `A3` | not connected | Left floating on purpose: its noise seeds `boot_id`. |

EMG electrodes (three, connected to the EMG module, not to the Arduino): two differential electrodes on the muscle belly, aligned with the direction of the fibres, and one reference electrode on a neutral bony area.

### Safety

- The EMG module probably needs **dual supply** (`+Vs` and `-Vs`) and its `SIG` output can exceed 5 V. Measure with a multimeter, at rest and with a maximum contraction, and limit the signal to 5 V (and above 0 V) before it reaches `A0`: the ATmega328P input does not tolerate more than Vcc or negative voltages.
- The GY-521 board accepts 5 V (it has a regulator); a bare MPU-6050 chip is 3.3 V.
- Do not connect electrodes to a person until the power supply and the isolation of the module have been verified.

### Step by step

1. Upload the sketch with the Arduino IDE: open `firmware/spine_wearable/spine_wearable.ino`, board Arduino Uno or Nano (on Nano clones choose the processor "ATmega328P (Old Bootloader)"), choose the port and upload.
2. Open the Serial Monitor at **250000 baud**. You should see `H,...` once, `T,...` every second, `D,E`/`D,I`/`D,S` every 100 ms, and `#imu ok` (or `#imu absent` if the MPU-6050 does not answer).
3. **Close the Serial Monitor**: the port is exclusive, and the gateway cannot open it while the IDE holds it.
4. Start the broker: `docker compose -f app/docker-compose.yml up -d mosquitto`.
5. Start the native gateway (see above) with the right port.
6. Watch the bus with `mosquitto_sub` (see Notes) or with a small aiomqtt script: `status` should go to `ok`, and background chunks of `imu_l3` and `stretch_lumbar` should arrive every second.
7. Publish the commands (JSON that validates against `telemetry/command`) on `.../cmd`: `start_session` (chunks at 100 ms and the EMG appears), `calibrate` of type `stretch` and `emg_mvc` (rest for the first, a maximum contraction for the second), `stop_session`. Check the acks and that the calibrations stay retained.
8. Check the stretch cord (resistance at rest and its change when stretched) and the IMU (about 1 g on the vertical axis at rest).

### Closing "raw or envelope EMG" (#13)

With the module connected, measure `SIG` with a multimeter (or watch `emg` in a session):

- If `SIG` rests at about 0 V and rises when the muscle contracts, the module delivers an **envelope**: keep `SPINE_GATEWAY_EMG_SIGNAL_KIND=envelope`.
- If `SIG` oscillates around a resting voltage, it delivers a **raw** signal: set `SPINE_GATEWAY_EMG_SIGNAL_KIND=raw` (and check that the resting voltage is really mid-scale, which is what the gateway assumes).

## Open points

- EMG raw or envelope (`SPINE_GATEWAY_EMG_SIGNAL_KIND`), pending the real module.
- Power supply of the EMG module and real range of `SIG`.
- `rest_length_mm` and the real curve of the stretch cord (#12); today they are a placeholder and a linear approximation.
- `calibrate` of type `imu` is not implemented.
- A dedicated error code for "not supported": it requires changing the contract (today `invalid_command` is used).
- Authentication and TLS of the broker (#96).
- BLE is out of scope.
