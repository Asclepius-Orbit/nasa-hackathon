**English** · [Español](README.es.md) · [Català](README.ca.md)

# spine-contracts (v0, draft)

Shared Pydantic models and MQTT topics. No concrete metrics or thresholds: Aguado defines those.

## Settled (for now)
- `SensorReading`: one reading = one scalar value of one sensor channel (`sensor_id` + `channel`). The IMU publishes 6 readings per sample. `quality` is a float 0..1.
- `Assessment`: engine output: `level` (green/amber/red), `risk_score` (0..1), `drivers`, `recommendations` (`REC_*` codes) and `escalate_to_ground`. `trend` is optional until the projection exists.
- Topics: `crew/{id}/spine/raw` (IMU and stretch sensor), `crew/{id}/emg/raw`, `crew/{id}/device/status`.

## Planned hardware
- MPU-6050 IMU: accel xyz (g) and gyro xyz (deg/s), I2C, 16 bit.
- Analog EMG with electrodes.
- Resistive stretch cord (~150 ohm/cm, non-linear, takes 1-2 min to recover).

## Open
- Actual payload of each sensor: raw or enveloped EMG; stretch in ohm, ADC counts or calibrated %.
- Frequency and batching: the IMU at ~100 Hz means a lot of MQTT messages.
- Exact semantics of `quality`.
- Baseline shape if Kalman is used.
- Derived metrics and thresholds (Aguado).
- Review: Andreea. Semantic validation: Aguado.
