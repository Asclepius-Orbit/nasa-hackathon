[English](README.md) · **Español** · [Català](README.ca.md)

# spine-contracts (v0, borrador)

Modelos Pydantic y topics MQTT compartidos. Sin métricas ni umbrales concretos: los define Aguado.

## Cerrado (por ahora)
- `SensorReading`: una lectura = un valor escalar de un canal (`sensor_id` + `channel`). La IMU publica 6 lecturas por muestra. `quality` es un float 0..1.
- `Assessment`: salida del motor, basada en el ejemplo de `arquitectura.md`. `trend` es opcional hasta que exista la proyección.
- Topics: `crew/{id}/spine/raw` (IMU y estiramiento), `crew/{id}/emg/raw`, `crew/{id}/device/status`.

## Hardware previsto
- IMU MPU-6050: accel xyz (g) y gyro xyz (deg/s), I2C, 16 bit.
- EMG analógico con electrodos.
- Cordón de estiramiento resistivo (~150 ohm/cm, no lineal, tarda 1-2 min en recuperarse).

## Abierto
- Payload real de cada sensor: EMG crudo o envolvente; estiramiento en ohm, cuentas ADC o % calibrado.
- Frecuencia y batching: la IMU a ~100 Hz son muchos mensajes MQTT.
- Semántica exacta de `quality`.
- Forma de la baseline si se usa Kalman.
- Métricas derivadas y umbrales (Aguado).
- Revisión: Andreea. Validación semántica: Aguado.
