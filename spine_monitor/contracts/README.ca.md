[English](README.md) · [Español](README.es.md) · **Català**

# spine-contracts (v0, esborrany)

Models Pydantic i topics MQTT compartits. Sense mètriques ni llindars concrets: els defineix Aguado.

## Tancat (per ara)
- `SensorReading`: una lectura = un valor escalar d'un canal (`sensor_id` + `channel`). La IMU publica 6 lectures per mostra. `quality` és un float 0..1.
- `Assessment`: sortida del motor, basada en l'exemple d'`arquitectura.md`. `trend` és opcional fins que existeixi la projecció.
- Topics: `crew/{id}/spine/raw` (IMU i estirament), `crew/{id}/emg/raw`, `crew/{id}/device/status`.

## Maquinari previst
- IMU MPU-6050: accel xyz (g) i gyro xyz (deg/s), I2C, 16 bit.
- EMG analògic amb elèctrodes.
- Cordó d'estirament resistiu (~150 ohm/cm, no lineal, triga 1-2 min a recuperar-se).

## Obert
- Payload real de cada sensor: EMG cru o envolvent; estirament en ohm, comptes ADC o % calibrat.
- Freqüència i batching: la IMU a ~100 Hz són molts missatges MQTT.
- Semàntica exacta de `quality`.
- Forma de la baseline si es fa servir Kalman.
- Mètriques derivades i llindars (Aguado).
- Revisió: Andreea. Validació semàntica: Aguado.
