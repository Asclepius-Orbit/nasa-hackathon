**English** · [Español](README.es.md) · [Català](README.ca.md)

# Wearable firmware

Arduino Uno / Nano sketch (ATmega328P, 16 MHz, 2 KB of RAM) that samples three sensors and ALWAYS
emits the v1 chunk protocol over USB serial (250000 baud, 8N1). It does not wait for commands from the host.
It is read by the real gateway (`spine_monitor/gateway`, see [its README](../spine_monitor/gateway/README.md)),
which publishes it on the MQTT v1 bus.

```
firmware/
  spine_wearable/spine_wearable.ino   sketch (Timer1 sampling + interrupt-driven ADC)
  spine_wearable/protocol.h           pure encoding (CRC-8, base64, frames), no Arduino
  tests/host_protocol_test.c          host parity test against the golden vectors
  tests/golden_lines.txt              golden vectors of the protocol
  Dockerfile                          pinned arduino-cli + avr core, gcc for the test
```

## Wiring

| Sensor | Arduino pin | Notes |
|---|---|---|
| EMG (Muscle Sensor V3, SIG output) | A0 | Assumed to be an analog envelope (not confirmed with the module, see below); 1000 Hz |
| Resistive stretch cord (Adafruit 519) | A1 | Divider: 5 V -> 10 kΩ resistor -> middle point (A1) -> cord -> GND; 20 Hz |
| MPU-6050 SDA | A4 | I2C, address 0x68, 400 kHz; 100 Hz |
| MPU-6050 SCL | A5 | |
| MPU-6050 VCC / GND | 5 V (or 3.3 V depending on the module) / GND | To confirm with the multimeter depending on the module (on-board regulator and pull-ups) |
| A3 | not connected | Left floating on purpose: its noise seeds the `boot_id` |

The three EMG electrodes (2 differential + reference) go to the module, not to the Arduino.

### EMG module power supply (to be confirmed)

The module pins are `+Vs`, `GND`, `-Vs` and `SIG`. The module very probably needs a
**dual supply** (+Vs and -Vs) and `SIG` may exceed 5 V. None of this is verified:
**confirm with the multimeter** before connecting `SIG` to the board (accepted supply voltage,
real range of `SIG` at rest and at maximum contraction).

The A0 input of the ATmega328P tolerates neither more than Vcc (5 V) nor negative voltages: the output
towards A0 must be limited to 5 V (for example a resistive divider plus a Zener or clamp diode at 5 V) if the
multimeter shows that `SIG` goes outside 0..5 V.

It is also unconfirmed whether the module outputs an envelope or a raw signal: the firmware samples A0 as is
and cannot tell; the gateway declares it with `SPINE_GATEWAY_EMG_SIGNAL_KIND` (default `envelope`).

## Protocol

ASCII lines ending in `\n`. Each line is `<body>*<XX>`, with `XX` the CRC-8/MAXIM of the body
(reflected polynomial 0x8C, init 0, no xorout) in 2 uppercase hex digits. Check: `CRC8("123456789") = 0xA1`.

| Line | When | Content |
|---|---|---|
| `H,1,<boot_id u32>,<fw_version>,<millis u32>` | At boot (the Uno resets when the port is opened) | `fw_version` = `0.1.0` |
| `T,<boot_id>,<millis>` | Every 1000 ms | |
| `D,E,<frame_seq u16>,<t0_ms u32>,100,<b64>` | Every 100 ms | 100 × uint16 LE (`analogRead`, 0..1023) at 1000 Hz |
| `D,I,<frame_seq>,<t0_ms>,10,<b64>` | Every 100 ms | 10 × (ax, ay, az, gx, gy, gz) int16 LE at 100 Hz |
| `D,S,<frame_seq>,<t0_ms>,2,<b64>` | Every 100 ms | 2 × uint16 LE at 20 Hz |
| `# ...` | Sporadic | Debug: `#imu ok`/`#imu absent` at boot, `#imu up`/`lost`/`late`, `#overrun`, `#adc busy`. No CRC: the gateway logs them at info level (`arduino: ...`) and does not count them as bad lines |

- `frame_seq` is independent per type, starts at 0 and wraps at 65536.
- `t0_ms` is the `millis()` of the instant of the batch's **first sample** (taken in the ISR, not when emitting).
- The MPU-6050 is configured with `PWR_MGMT_1=0`, DLPF 44 Hz (`CONFIG=0x03`), ±500 dps (`GYRO_CONFIG=0x08`) and ±4 g (`ACCEL_CONFIG=0x08`).
- Standard base64 with padding.

Examples (golden vectors, the same ones the test checks):

```
H,1,2893746120,0.1.0,1234*15
T,2893746120,5000*76
D,E,7,1000,4,AAABAAAC/wM=*09
D,I,3,2000,2,ZACc/wAgAAD///9/AIAFAPv/QQC//+gD*E6
D,S,65535,4294967295,2,/wEAAg==*E8
```

### Behaviours to be aware of

- **MPU absent at boot:** no `I` frames are emitted, `E`, `S` and `T` continue, and it is reported with
  `#imu absent`. The configuration is retried every second; if it answers, `#imu up` appears and the `I`
  frames start. The gateway will see the `I` stream as `stalled`. The same happens if the MPU is lost
  while running (more than 20 consecutive failed reads, `#imu lost`).
- **Batch not yet emitted when the next one arrives (overrun):** the new one is dropped, counted, reported once
  with `#overrun`, and its `frame_seq` is consumed, so the host sees a jump in `frame_seq`.
- **IMU with a delayed `loop()`:** if 10 ms periods pile up without reading, it reads once and that
  sample is repeated in the gaps (each gap keeps its position in the batch; nothing shifts). Reported with `#imu late`.
- **`boot_id`:** a u32 that changes at every boot. It is obtained by mixing (MurmurHash3 fmix32) a
  boot counter stored in EEPROM[0..3], the low bit of 32 ADC readings on the floating A3
  and `micros()`.
- **If the host does not read:** the Arduino does not block; the USB-serial chip drops what is not collected
  (behaviour of the bridge, to confirm on the specific board).

## Build with Docker

From the repository root:

```bash
docker compose -f app/docker-compose.yml --profile firmware run --rm firmware-build
```

It runs, in this order, and fails if any step fails:

1. Host parity test (gcc): `protocol.h` must reproduce the vectors of `tests/golden_lines.txt`
   byte by byte and `CRC8("123456789") == 0xA1`.
2. `arduino-cli compile --fqbn arduino:avr:uno --warnings all firmware/spine_wearable`, which prints the
   flash and RAM usage.

The image pins `arduino-cli` 1.6.0 (sha256 verified) and the `arduino:avr@1.8.6` core. No third-party
libraries: only `Wire` and `EEPROM` from the core. The service mounts `firmware/` from the host, so the image
does not need rebuilding when the code is edited (it does when the `Dockerfile` changes: add `--build`).

The build context is the repository root (`context: ..` in the compose file). If Docker fails to read it because of
unreadable folders (on Windows, leftover temporary directories with broken permissions), delete them or
build from a clean clone.

## Upload with the Arduino IDE

1. Open `firmware/spine_wearable/spine_wearable.ino` (the folder must be named like the `.ino`).
2. Tools > Board: **Arduino Uno** or **Arduino Nano** (on a Nano, the processor according to its
   bootloader: ATmega328P or ATmega328P (Old Bootloader)).
3. Choose the port and click Upload.
4. Serial Monitor at **250000 baud** to see the lines. Close it before starting the gateway: the port is exclusive.

Wiring, testing with the real board and the step-by-step safety notes are in the
[gateway README](../spine_monitor/gateway/README.md).
