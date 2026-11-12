[English](README.md) · **Español** · [Català](README.ca.md)

# Firmware del wearable

Sketch Arduino Uno / Nano (ATmega328P, 16 MHz, 2 KB de RAM) que muestrea tres sensores y emite
SIEMPRE por USB serie (250000 baud, 8N1) el protocolo de chunks v1. No espera órdenes del host.
Quien lo lee es el gateway real (`spine_monitor/gateway`, ver [su README](../spine_monitor/gateway/README.es.md)),
que lo publica en el bus MQTT v1.

```
firmware/
  spine_wearable/spine_wearable.ino   sketch (muestreo por Timer1 + ADC por interrupción)
  spine_wearable/protocol.h           codificación pura (CRC-8, base64, tramas), sin Arduino
  tests/host_protocol_test.c          test de paridad en host contra los vectores dorados
  tests/golden_lines.txt              vectores dorados del protocolo
  Dockerfile                          arduino-cli + core avr fijados, gcc para el test
```

## Cableado

| Sensor | Pin del Arduino | Notas |
|---|---|---|
| EMG (Muscle Sensor V3, salida SIG) | A0 | Se supone envolvente analógica (sin confirmar con el módulo, ver más abajo); 1000 Hz |
| Cordón elástico resistivo (Adafruit 519) | A1 | Divisor: 5 V -> resistencia de 10 kΩ -> punto medio (A1) -> cordón -> GND; 20 Hz |
| MPU-6050 SDA | A4 | I2C, dirección 0x68, 400 kHz; 100 Hz |
| MPU-6050 SCL | A5 | |
| MPU-6050 VCC / GND | 5 V (o 3.3 V según el módulo) / GND | A confirmar con el multímetro según el módulo (regulador y pull-ups a bordo) |
| A3 | sin conectar | Se deja flotante a propósito: su ruido alimenta el `boot_id` |

Los tres electrodos del EMG (2 diferenciales + referencia) van al módulo, no al Arduino.

Colocación (protocolo estandarizado de jalón al pecho): el MPU-6050 va en T9 y el cordón elástico va de C3 a S1 (~650 mm en reposo, PENDIENTE medición Aguado con ohmímetro). El protocolo declara dos módulos EMG (dorsal ancho izquierdo y derecho), pero el hardware actual tiene un solo canal EMG (A0) y el gateway publica uno (`emg_lat_left`, `SPINE_GATEWAY_EMG_STREAM_ID` / `SPINE_GATEWAY_EMG_SIDE`) hasta que Polfer confirme el hardware (#13). El formato de trama serie no cambia. Nota permanente de electrodos: ver el protocolo en los contratos.

### Alimentación del módulo EMG (a confirmar)

Los pines del módulo son `+Vs`, `GND`, `-Vs` y `SIG`. Es muy probable que el módulo necesite
**alimentación dual** (+Vs y -Vs) y que `SIG` pueda superar los 5 V. Nada de esto está verificado:
**a confirmar con el multímetro** antes de conectar `SIG` a la placa (tensión de alimentación
admitida, rango real de `SIG` en reposo y con contracción máxima).

La entrada A0 del ATmega328P no tolera más de Vcc (5 V) ni tensiones negativas: la salida hacia A0
debe limitarse a 5 V (por ejemplo divisor resistivo más un diodo Zener o de clamp a 5 V) si el
multímetro muestra que `SIG` sale de 0..5 V.

También está sin confirmar si el módulo entrega envolvente o señal cruda: el firmware muestrea A0 tal cual
y no distingue; el gateway lo declara con `SPINE_GATEWAY_EMG_SIGNAL_KIND` (por defecto `envelope`).

## Protocolo

Líneas ASCII terminadas en `\n`. Cada línea es `<cuerpo>*<XX>`, con `XX` el CRC-8/MAXIM del cuerpo
(polinomio reflejado 0x8C, init 0, sin xorout) en 2 hex mayúscula. Comprobación: `CRC8("123456789") = 0xA1`.

| Línea | Cuándo | Contenido |
|---|---|---|
| `H,1,<boot_id u32>,<fw_version>,<millis u32>` | Al arrancar (el Uno se resetea al abrir el puerto) | `fw_version` = `0.1.0` |
| `T,<boot_id>,<millis>` | Cada 1000 ms | |
| `D,E,<frame_seq u16>,<t0_ms u32>,100,<b64>` | Cada 100 ms | 100 × uint16 LE (`analogRead`, 0..1023) a 1000 Hz |
| `D,I,<frame_seq>,<t0_ms>,10,<b64>` | Cada 100 ms | 10 × (ax, ay, az, gx, gy, gz) int16 LE a 100 Hz |
| `D,S,<frame_seq>,<t0_ms>,2,<b64>` | Cada 100 ms | 2 × uint16 LE a 20 Hz |
| `# ...` | Esporádica | Depuración: `#imu ok`/`#imu absent` al arrancar, `#imu up`/`lost`/`late`, `#overrun`, `#adc busy`. Sin CRC: el gateway las registra en el log a nivel info (`arduino: ...`) y no las cuenta como línea mala |

- `frame_seq` es independiente por tipo, empieza en 0 y da la vuelta en 65536.
- `t0_ms` es el `millis()` del instante de la **primera muestra** del lote (se toma en la ISR, no al emitir).
- El MPU-6050 se configura con `PWR_MGMT_1=0`, DLPF 44 Hz (`CONFIG=0x03`), ±500 dps (`GYRO_CONFIG=0x08`) y ±4 g (`ACCEL_CONFIG=0x08`).
- Base64 estándar con padding.

Ejemplos (vectores dorados, los mismos que comprueba el test):

```
H,1,2893746120,0.1.0,1234*15
T,2893746120,5000*76
D,E,7,1000,4,AAABAAAC/wM=*09
D,I,3,2000,2,ZACc/wAgAAD///9/AIAFAPv/QQC//+gD*E6
D,S,65535,4294967295,2,/wEAAg==*E8
```

### Comportamientos a tener en cuenta

- **MPU ausente al arrancar:** no se emiten tramas `I`, se sigue con `E`, `S` y `T`, y se avisa con
  `#imu absent`. Cada segundo se reintenta la configuración; si responde, aparece `#imu up` y empiezan
  las tramas `I`. El gateway verá el stream `I` como `stalled`. Lo mismo ocurre si el MPU se pierde
  en marcha (más de 20 lecturas fallidas seguidas, `#imu lost`).
- **Lote sin emitir cuando llega el siguiente (overrun):** se descarta el nuevo, se cuenta, se avisa una
  vez con `#overrun` y su `frame_seq` se consume, así que el host ve un salto de `frame_seq`.
- **IMU con `loop()` retrasado:** si se acumulan periodos de 10 ms sin leer, se lee una vez y esa
  muestra se repite en los huecos (cada hueco conserva su posición en el lote; nada se desplaza). Se avisa con `#imu late`.
- **`boot_id`:** u32 que cambia en cada arranque. Se obtiene mezclando (MurmurHash3 fmix32) un
  contador de arranques guardado en EEPROM[0..3], el bit bajo de 32 lecturas del ADC en A3 flotante
  y `micros()`.
- **Si el host no lee:** el Arduino no se bloquea; el chip USB-serie descarta lo que no se recoge
  (comportamiento del puente, a confirmar en la placa concreta).

## Compilar con Docker

Desde la raíz del repo:

```bash
docker compose -f app/docker-compose.yml --profile firmware run --rm firmware-build
```

Ejecuta, en este orden, y falla si cualquiera falla:

1. Test de paridad en host (gcc): `protocol.h` debe reproducir los vectores de `tests/golden_lines.txt`
   byte a byte y `CRC8("123456789") == 0xA1`.
2. `arduino-cli compile --fqbn arduino:avr:uno --warnings all firmware/spine_wearable`, que imprime el
   uso de flash y RAM.

La imagen fija `arduino-cli` 1.6.0 (sha256 verificado) y el core `arduino:avr@1.8.6`. Sin librerías de
terceros: solo `Wire` y `EEPROM` del core. El servicio monta `firmware/` desde el host, así que no hace
falta reconstruir la imagen al editar el código (sí al cambiar el `Dockerfile`: añade `--build`).

El contexto de build es la raíz del repo (`context: ..` en el compose). Si Docker falla al leerlo por
carpetas ilegibles (en Windows, restos de directorios temporales con permisos rotos), elimínalas o
construye desde un clon limpio.

## Subir con Arduino IDE

1. Abrir `firmware/spine_wearable/spine_wearable.ino` (la carpeta debe llamarse igual que el `.ino`).
2. Herramientas > Placa: **Arduino Uno** o **Arduino Nano** (en un Nano, el procesador según su
   bootloader: ATmega328P u ATmega328P (Old Bootloader)).
3. Elegir el puerto y pulsar Subir.
4. Monitor serie a **250000 baud** para ver las líneas. Cerrarlo antes de arrancar el gateway: el puerto es exclusivo.

El cableado, la prueba con la placa real y los avisos de seguridad paso a paso están en el
[README del gateway](../spine_monitor/gateway/README.es.md).
