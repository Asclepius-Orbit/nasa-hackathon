[English](README.md) · **Español** · [Català](README.ca.md)

# spine-gateway

Gateway real del wearable. Lee el Arduino Uno/Nano del wearable por USB serie y publica en el bus MQTT v1 lo que define el contrato: chunks de stream, estado del dispositivo, sincronización de reloj, calibraciones y acks de comandos. Su comportamiento observable es el mismo que el del gateway falso de `spine_monitor/physiology`, de modo que el backend, el motor y la UI no los distinguen (salvo por `source: device` en lugar de `simulator`).

## Estado de verificación

Verificado **sin hardware**:

- Tests unitarios de todos los módulos (protocolo, ensamblador, calibración, comandos, estado, fuente serie, app, emulador, ajustes) y tests de propiedades del protocolo y del ensamblador.
- Emulador del Arduino (`spine-arduino-emulator`) que habla el protocolo serie por TCP.
- Test de extremo a extremo contra un Mosquitto real: emulador -> gateway -> broker -> observador, con validación completa de esquema, topics, QoS, retained y acks.
- Build del firmware (`firmware-build`): test de paridad de `protocol.h` contra los vectores dorados y compilación para `arduino:avr:uno`.

**Aún no probado con una placa real.** Nada de esto se ha ejecutado en hardware:

- Temporización real del muestreo del ADC y de la ISR de Timer1 (EMG a 1000 Hz, cordón a 20 Hz).
- Lectura I2C del MPU-6050 (100 Hz, 400 kHz).
- Caudal del puente USB-serie a 250000 baud (unos 5 KB/s de carga media).
- El módulo EMG real: alimentación, rango de `SIG` y si entrega señal cruda o envolvente.
- Ruido del divisor de tensión del cordón.
- Reinicio del Uno al abrir el puerto serie (el gateway lo da por hecho y espera al siguiente hello/tick).
- Paso del dispositivo USB a Docker en Linux (servicio `gateway`, perfil `hardware`).
- SIGTERM del emulador en Docker.

## Arquitectura

Paquete `spine_gateway` (`src/spine_gateway/`):

| Módulo | Qué hace |
|---|---|
| `settings.py` | `GatewaySettings`: todas las variables `SPINE_GATEWAY_*` (o `.env`) con validación de ids contra `spine_contracts.topics`. |
| `protocol.py` | Protocolo de línea serie: parser (`parse_line`, nunca lanza) y codificadores, CRC-8/MAXIM, `frame_gap` sobre el contador `u16`. |
| `ports.py` | Puerto `LineSource` y sus eventos (`SerialLine`, `LinkUp`, `LinkDown`). |
| `serial_source.py` | `SerialLineSource`: pyserial en un hilo, trocea por `\n`, reconecta con backoff exponencial. |
| `assembler.py` | `ChunkAssembler`: convierte los lotes `D` en `EmgChunk`/`ImuChunk`/`StretchChunk` (unidades, calidad, `seq`, huecos, ventanas de fondo a 10 Hz). |
| `calibration.py` | `CalibrationRecorder`: graba las calibraciones `stretch` y `emg_mvc` a partir de los lotes. |
| `commands.py` | Lee y valida los payloads de `cmd` y construye los `command-ack`. |
| `status.py` | `DeviceStatus`, `ClockSync` y el `session_id` de fondo; estado de streams y global. |
| `app.py` | `Gateway`: bucle MQTT (aiomqtt), Last Will, temporizadores, comandos, sesiones y calibraciones. |
| `emulator.py` | Arduino falso por TCP (`spine-arduino-emulator`) para trabajar sin placa. |
| `__main__.py` | Punto de entrada `spine-gateway`. |

Flujo de datos:

```
Arduino (firmware/spine_wearable) --USB serie, 250000 baud--> líneas H / T / D (+ CRC-8)
  -> SerialLineSource -> Gateway (parse_line, ChunkAssembler, CalibrationRecorder)
  -> MQTT v1 (aiomqtt, QoS 1) -> Mosquitto
```

## Cómo ejecutarlo

Los comandos parten de la raíz del repo; los de `uv` se ejecutan desde `spine_monitor/`.

### Nativo, con el Arduino real

Levanta el broker (solo escucha en `127.0.0.1:1883`) y el gateway:

```bash
docker compose -f app/docker-compose.yml up -d mosquitto
cd spine_monitor
SPINE_GATEWAY_SERIAL_URL=/dev/ttyACM0 uv run spine-gateway      # Linux
```

```powershell
cd spine_monitor
$env:SPINE_GATEWAY_SERIAL_URL = "COM3"; uv run spine-gateway    # Windows
```

El broker se espera en `127.0.0.1:1883` (`SPINE_GATEWAY_MQTT_HOST` / `SPINE_GATEWAY_MQTT_PORT`). Las variables también pueden ir en un `.env` en el directorio desde el que se arranca el gateway. `Ctrl+C` (o SIGTERM) deja un estado `offline` retained y cierra el puerto y la conexión.

### Sin hardware: emulador

Dos terminales, con el broker levantado:

```bash
cd spine_monitor
uv run spine-arduino-emulator --port 5000
SPINE_GATEWAY_SERIAL_URL=socket://127.0.0.1:5000 uv run spine-gateway
```

pyserial convierte `socket://host:puerto` en una conexión TCP, así que el gateway ejecuta exactamente el mismo código que con una placa. El emulador genera señales sintéticas deterministas y da a cada cliente un dispositivo nuevo (`boot_id` nuevo, contadores a cero), como un Arduino reseteado al abrir el puerto. Opciones: `--host` (por defecto `127.0.0.1`), `--port` (por defecto 5000), `--seed`, `--no-imu` (firmware sin MPU-6050: sin tramas `I` y con `#imu absent`), `--drop-every N` (cada N-ésimo lote no sale pero gasta su `frame_seq`, como un overrun) y `--reboot-after S` (reinicio del dispositivo a los S segundos).

La misma demo en Docker, perfil `gateway-demo` (emulador + gateway; Mosquitto arranca como dependencia). La imagen `spine-gateway:dev` se construye en el primer `up` (usa `--build` tras cambiar el código):

```bash
docker compose -f app/docker-compose.yml --profile gateway-demo up -d arduino-emulator gateway-demo
```

### Placa real en Docker (solo Linux)

Perfil `hardware`, servicio `gateway`:

```bash
docker compose -f app/docker-compose.yml --profile hardware up -d gateway
```

El dispositivo se mapea a `/dev/ttyACM0` dentro del contenedor; `SPINE_GATEWAY_SERIAL_DEVICE` elige el dispositivo del host (por defecto `/dev/ttyACM0`). Solo funciona en Linux: Docker Desktop en Windows y macOS no puede pasar un dispositivo USB a un contenedor, así que allí el gateway se ejecuta de forma nativa.

### Notas

- El broker local no tiene autenticación y solo escucha en `127.0.0.1`. Exponerlo en la red exige autenticación antes (issue #96, fuera de alcance aquí).
- Observar el bus: `docker compose -f app/docker-compose.yml exec mosquitto mosquitto_sub -h 127.0.0.1 -t 'asclepius/v1/crew/AST-01/device/wb-01/#' -v` (ids de la configuración por defecto).

## Configuración

Todas las variables llevan el prefijo `SPINE_GATEWAY_` y son opcionales; `.env.example` las lista comentadas. Los ids se validan con las mismas expresiones que usa `spine_contracts.topics` para construir los topics.

| Variable | Por defecto | Qué hace |
|---|---|---|
| `SPINE_GATEWAY_SERIAL_URL` | `COM3` | Puerto serie tal como lo entiende pyserial: `COM3`, `/dev/ttyACM0` o `socket://host:puerto`. |
| `SPINE_GATEWAY_SERIAL_BAUD` | `250000` | Velocidad en baudios (el firmware está fijado a 250000). |
| `SPINE_GATEWAY_SERIAL_RECONNECT_INITIAL_S` | `1.0` | Primera espera (s) antes de reabrir el puerto si no abre o se desenchufa. |
| `SPINE_GATEWAY_SERIAL_RECONNECT_FACTOR` | `2.0` | Multiplicador de la espera tras cada intento fallido (>= 1). |
| `SPINE_GATEWAY_SERIAL_RECONNECT_MAX_S` | `10.0` | Tope (s) de la espera de reconexión serie (>= la inicial). |
| `SPINE_GATEWAY_ASTRONAUT_ID` | `AST-01` | Astronauta al que se atribuyen los datos (va en los topics). |
| `SPINE_GATEWAY_DEVICE_ID` | `wb-01` | Id del dispositivo (va en los topics y en el id de cliente MQTT). |
| `SPINE_GATEWAY_MQTT_HOST` | `127.0.0.1` | Host del broker MQTT. |
| `SPINE_GATEWAY_MQTT_PORT` | `1883` | Puerto del broker MQTT (1-65535). |
| `SPINE_GATEWAY_MQTT_CLIENT_ID` | `spine-gateway` | Prefijo del id de cliente MQTT; la conexión usa `<client_id>-<device_id>`. |
| `SPINE_GATEWAY_MQTT_RECONNECT_INITIAL_S` | `1.0` | Primera espera (s) antes de reconectar con el broker. |
| `SPINE_GATEWAY_MQTT_RECONNECT_FACTOR` | `2.0` | Multiplicador de la espera tras cada intento fallido (>= 1); vuelve a la inicial al conectar. |
| `SPINE_GATEWAY_MQTT_RECONNECT_MAX_S` | `30.0` | Tope (s) de la espera de reconexión MQTT (>= la inicial). |
| `SPINE_GATEWAY_STATUS_INTERVAL_S` | `30.0` | Periodo (s) del mensaje `status` (también se publica en cuanto cambia algo relevante). |
| `SPINE_GATEWAY_CLOCK_SYNC_INTERVAL_S` | `60.0` | Periodo (s) del mensaje `clock`. |
| `SPINE_GATEWAY_CLOCK_UNCERTAINTY_MS` | `20.0` | Error estimado (ms) del ancla de reloj: hora de llegada del tick al gateway (latencia USB + lectura de línea). |
| `SPINE_GATEWAY_STALL_TIMEOUT_S` | `2.0` | Un stream sin lotes durante este tiempo (s) pasa a `stalled` y el dispositivo a `degraded`. |
| `SPINE_GATEWAY_CALIBRATION_DEFAULT_DURATION_S` | `5.0` | Duración (s) de un `calibrate` sin `duration_s`. |
| `SPINE_GATEWAY_EMG_STREAM_ID` | `emg_lat_left` | `stream_id` del EMG. |
| `SPINE_GATEWAY_IMU_STREAM_ID` | `imu_l3` | `stream_id` del IMU. |
| `SPINE_GATEWAY_STRETCH_STREAM_ID` | `stretch_lumbar` | `stream_id` del cordón elástico. |
| `SPINE_GATEWAY_EMG_MUSCLE` | `latissimus_dorsi` | Músculo donde va el electrodo EMG (enum `Muscle` del contrato); va en la calibración `emg_mvc`. |
| `SPINE_GATEWAY_EMG_SIDE` | `left` | Lado del electrodo (`left`, `right` o `midline`). |
| `SPINE_GATEWAY_ADC_BITS` | `10` | Resolución del ADC (el ATmega328P tiene 10 bits). |
| `SPINE_GATEWAY_VREF_V` | `5.0` | Tensión de referencia del ADC (V). |
| `SPINE_GATEWAY_EMG_INPUT_PIN` | `A0` | Pin de entrada del EMG, declarado en el chunk (`acquisition.input_pin`). |
| `SPINE_GATEWAY_STRETCH_INPUT_PIN` | `A1` | Pin de entrada del cordón, declarado en el chunk (`circuit.input_pin`). |
| `SPINE_GATEWAY_STRETCH_SERIES_RESISTOR_OHM` | `10000.0` | Resistencia fija del divisor del cordón (ohm); sirve para calcular `resistance_ohm`. |
| `SPINE_GATEWAY_IMU_ACCEL_RANGE_G` | `4` | Rango del acelerómetro (g) usado para convertir cuentas: 2, 4, 8 o 16. Debe coincidir con el firmware (fijo en 4). |
| `SPINE_GATEWAY_IMU_GYRO_RANGE_DPS` | `500` | Rango del giróscopo (°/s): 250, 500, 1000 o 2000. Debe coincidir con el firmware (fijo en 500). |
| `SPINE_GATEWAY_IMU_DLPF_HZ` | `44` | Filtro paso bajo digital del MPU-6050, declarado en el chunk (`config.dlpf_hz`). El firmware configura 44 Hz. |
| `SPINE_GATEWAY_EMG_GAIN` | `1.0` | Ganancia del módulo EMG; los mV se dividen entre ella. |
| `SPINE_GATEWAY_EMG_SIGNAL_KIND` | `envelope` | Qué entrega el módulo EMG al ADC: `raw`, `rectified` o `envelope`. El valor por defecto es una suposición, no está confirmado con la placa (ver abajo). |
| `SPINE_GATEWAY_STRETCH_REST_LENGTH_MM` | `100.0` | Longitud (mm) del cordón en reposo, usada por la calibración `stretch`. Marcador de posición hasta medirla (#12). |
| `SPINE_GATEWAY_EMG_MVC_WINDOW_MS` | `250` | Ventana (ms) del RMS deslizante de la calibración `emg_mvc`. |

`SPINE_GATEWAY_EMG_SIGNAL_KIND` fija `signal_kind` en los chunks EMG y en la calibración `emg_mvc`, y decide cómo se calculan los mV y la calidad:

- `envelope` (por defecto) y `rectified`: señal unipolar. mV = `cuentas / 1023 * vref * 1000 / gain`. `clipping` solo en el máximo del ADC (0 es un músculo en reposo, un valor sano).
- `raw`: señal bipolar que se supone polarizada a media escala (`(2^bits - 1) / 2`, 511,5 cuentas). El gateway resta ese offset antes de convertir a mV, y el RMS de la MVC se calcula sobre la señal con el offset restado. `clipping` en 0 y en el máximo. La polarización a media escala es una suposición sin verificar.

## Comportamiento MQTT

Los topics se construyen siempre con `spine_contracts.topics` (raíz `asclepius/v1/crew/{astronaut_id}/device/{device_id}/...`). Todos los mensajes son QoS 1. Un dispositivo por proceso.

| Topic | Dirección | Retained | Contenido |
|---|---|---|---|
| `.../stream/{stream_id}` | gateway -> bus | no | `StreamChunk` (EMG, IMU, cordón). |
| `.../status` | gateway -> bus | sí | `DeviceStatus`. |
| `.../clock` | gateway -> bus | sí | `ClockSync`. |
| `.../calibration/{stream_id}` | gateway -> bus | sí | Calibración vigente del stream. |
| `.../ack` | gateway -> bus | no | `CommandAck`. |
| `.../cmd` | bus -> gateway | no | `Command` (el gateway se suscribe). |

No se publica nada del dispositivo hasta que llega el primer hello o tick tras abrir el puerto (abrirlo reinicia el Uno).

### Estado

`status` se publica retained cada `status_interval_s`, justo tras cada conexión MQTT y en cuanto cambia algo relevante (estado global, `boot_id` nuevo, un stream que se para o vuelve, cambio de frecuencia). Lleva `source: device`, `boot_id`, `uptime_ms`, `firmware_version` (del hello), `link: usb_serial` y una entrada por stream con `state`, `sample_rate_hz` y `last_seq`. Sin batería ni temperatura: el Arduino no las mide.

- Estado global: `offline` con el enlace serie caído o sin ningún hello/tick desde que se abrió el puerto; `degraded` si algún stream está `stalled`; si no, `ok`.
- Estado de stream: `active` (llegó un lote hace menos de `stall_timeout_s`), `stalled` (participa y lleva `stall_timeout_s` sin lotes, o nunca mandó ninguno pasado ese margen; por ejemplo el firmware no emite tramas `I` si el MPU-6050 no responde) y `disabled` (no participa, es decir, el EMG fuera de una sesión que lo incluya, o aún sin lotes y sin haber pasado el margen).
- Last Will: `offline` retained, QoS 1, registrado al conectar. Una parada limpia (SIGTERM, Ctrl+C) publica `offline` retained de forma explícita, porque el Last Will solo salta si se pierde la conexión.

### Sincronización de reloj

`clock` lleva `method: gateway_receive_time`: los `millis()` del último tick del dispositivo y la hora UTC a la que llegó al gateway, con `uncertainty_ms = SPINE_GATEWAY_CLOCK_UNCERTAINTY_MS`. Se publica tras cada conexión MQTT, cada `clock_sync_interval_s`, al empezar una sesión y en el primer tick tras un `boot_id` nuevo.

### Chunks

- **Sesión** (entre `start_session` y su fin): un chunk por lote de 100 ms, a frecuencia nativa: EMG 1000 Hz (100 muestras), IMU 100 Hz (10), cordón 20 Hz (2). Si se omite `stream_ids` participan los tres. Un stream que no está en la sesión sigue como fondo.
- **Fondo** (fuera de sesión, solo IMU y cordón): la media de cada lote, 10 lotes contiguos por chunk, chunks de 1 s a 10 Hz. El EMG no se publica en fondo. Su `session_id` es un uuid5 determinista del dispositivo y del día UTC (cambia a las 00:00 UTC).
- **`seq`**: por `boot_id` y stream, empieza en 0 y lo comparten sesión y fondo. Un `boot_id` nuevo lo reinicia.
- **Huecos**: cada lote perdido consume un `seq`; el siguiente chunk de sesión declara `dropped_samples` (lotes perdidos x muestras por lote), lleva el flag `packet_gap` y una puntuación de calidad como mucho de 0,7. En fondo un hueco descarta la ventana parcial, consume el `seq` de las ventanas de 1 s que toca y marca `packet_gap` (sin `dropped_samples`). Los lotes duplicados, los que llegan desordenados y los que traen un número de muestras inesperado se descartan.
- **Unidades**: EMG en mV en la entrada del ADC (ver `EMG_SIGNAL_KIND`); IMU en g y °/s con las sensibilidades nominales del MPU-6050 para el rango configurado; cordón en `adc_counts` más `resistance_ohm = Rs * cuentas / (max - cuentas)`. Si alguna muestra de un chunk del cordón está en 0 o en el máximo (corto o circuito abierto) el chunk omite `resistance_ohm` y `length_mm`. `length_mm` y `calibration_id` aparecen solo mientras hay una calibración `stretch` vigente (curva lineal por tramos, recortada en sus extremos con el flag `out_of_calibration`).
- **Calidad** (`assessed_by: gateway`): `score` = muestras sin problema / muestras. Flags: `clipping` (EMG en el máximo del ADC, y en 0 si es `raw`; cordón en 0 o en el máximo; IMU en -32768 o 32767 en algún eje), `lead_off` (cordón en el máximo: circuito abierto), `packet_gap` y `out_of_calibration`.

### Comandos

El gateway se suscribe a `.../cmd`. Tipos: `start_session`, `stop_session`, `mark_event` y `calibrate`. Cada comando recibe acks en `.../ack`:

| Comando | Acks, en orden |
|---|---|
| `start_session` | `accepted`; más tarde `completed` cuando acaba la sesión (`stop_session` o `max_duration_s`). |
| `stop_session` | `accepted`; `completed` del comando `start_session`; `completed` del `stop_session`. |
| `mark_event` | `accepted`, `completed`. Sin efecto en el dispositivo. |
| `calibrate` | `accepted`; la calibración se publica retained; `completed` con `result.calibration_id` (el del primer stream), o `failed`. |

Rechazos (`rejected`, `error.code`):

| Código | Cuándo |
|---|---|
| `busy` | `start_session` con una sesión en curso; `calibrate` con otra calibración en curso. |
| `unknown_stream` | Un `stream_id` que no existe; en `calibrate`, un stream de otro tipo (`stretch` necesita el stream del cordón, `emg_mvc` el del EMG). |
| `no_session` | `stop_session` o `mark_event` de una sesión que no es la que corre. |
| `device_offline` | `start_session` o `calibrate` con el dispositivo sin conectar. |
| `invalid_command` | Payload inválido (JSON Schema), ids que no coinciden con el topic, una duración de más de 30 días, un payload de más de 16 KB, o `calibrate` de tipo `imu`. |
| `internal` | Error inesperado (el ack es `failed`). |

Otras reglas:

- Un payload sin `command_id` (UUID) legible no se puede responder y se ignora. Un payload de más de 16 KB (16384 bytes) ni se parsea: si se encuentra su `command_id` se rechaza con `invalid_command`.
- **Deduplicación:** se recuerdan los últimos 256 `command_id`. Una reentrega QoS 1 recibe de nuevo los mismos acks y no se ejecuta dos veces.
- `calibrate` admite `stretch` y `emg_mvc`; `imu` se rechaza con `invalid_command` (el enum de `error.code` no tiene "no soportado"). Graba durante `duration_s` (por defecto `SPINE_GATEWAY_CALIBRATION_DEFAULT_DURATION_S`) de **tiempo de dispositivo**; no exige que `session_id` sea la sesión en curso. Si los datos no están completos 5 s después de `duration_s`, falla.
- `calibration_failed` y `calibration_timeout` viajan ambos como `error.code = "calibration_failed"`; el detalle va en el mensaje: `<motivo>: <detalle>`, con motivo `incomplete`, `rest_resistance_not_computable`, `too_few_samples`, `flat_signal` o `calibration_timeout`. Los mensajes se cortan a 300 caracteres.
- Las calibraciones viven en la memoria del gateway y se republican retained tras cada reconexión MQTT, antes del siguiente chunk que las cite. Reiniciar el gateway arranca sin calibración.
- `stretch`: `rest_resistance_ohm` es la resistencia calculada con la media de las cuentas; `rest_length_mm` sale de la configuración; la curva es la **aproximación por defecto**, una lineal `R = R0 * L / L0` con puntos en `L0 * {1.0, 1.25, 1.5, 1.75, 2.0}`. No está medida: el cordón no es lineal y tiene histéresis, y la curva medida de #12 debe sustituirla.
- `emg_mvc`: `mvc_rms_mv` es el máximo del RMS deslizante (`SPINE_GATEWAY_EMG_MVC_WINDOW_MS`) en mV.

## Protocolo serie

Líneas ASCII terminadas en `\n`, cada una `<cuerpo>*<XX>` con `XX` el CRC-8/MAXIM del cuerpo en 2 hex mayúscula. El base64 es estándar con padding. Las líneas de más de 1024 caracteres se descartan.

| Línea | Contenido |
|---|---|
| `H,1,<boot_id>,<fw_version>,<millis>` | Hello tras el arranque (`1` = versión del protocolo). |
| `T,<boot_id>,<millis>` | Tick cada 1000 ms, ancla del reloj del dispositivo. |
| `D,E,<frame_seq>,<t0_ms>,100,<b64>` | EMG: 100 x `uint16` LE, 1000 Hz, cada 100 ms. |
| `D,I,<frame_seq>,<t0_ms>,10,<b64>` | IMU: 10 x (ax, ay, az, gx, gy, gz) `int16` LE, 100 Hz. |
| `D,S,<frame_seq>,<t0_ms>,2,<b64>` | Cordón: 2 x `uint16` LE, 20 Hz. |
| `# ...` | Texto de depuración del firmware. No lleva CRC, así que el gateway no lo interpreta: lo registra en el log a nivel info (`arduino: #imu ok`) y no lo cuenta como línea mala. |

Las líneas no válidas (CRC malo, campo malo, base64 malo...) se cuentan por motivo, se avisan en la 1.ª, 2.ª, 4.ª, 8.ª... ocurrencia (el resto a nivel debug) y se descartan. El detalle completo (cableado, comportamientos del firmware, ejemplos) está en [`firmware/README.es.md`](../../firmware/README.es.md).

## Tests

Desde `spine_monitor/`:

```bash
uv run pytest gateway/tests -q -p no:randomly                  # todo; los tests de broker se saltan sin broker
uv run pytest gateway/tests -q -p no:randomly -m mosquitto     # solo los que usan un broker real
```

Los tests `mosquitto` y el E2E con emulador (`test_e2e_emulator_mosquitto.py`) necesitan un broker en `127.0.0.1:1883` (`docker compose -f app/docker-compose.yml up -d mosquitto`); sin él se saltan. Con `SPINE_REQUIRE_BROKER=1` fallan en vez de saltarse, que es como los ejecuta el gate. Usan device ids propios (`itest-*`) y borran los retained que dejan.

Gate de calidad (ruff, pyright, tests con cobertura y mutación incremental), igual en local y en CI:

```bash
GATE_MUTATION=changed docker compose -f app/docker-compose.yml run --rm quality
```

El paquete `gateway` es nuevo en el workspace, así que la imagen `spine-quality` hay que reconstruirla una vez antes de la primera ejecución: `docker compose -f app/docker-compose.yml build quality`.

Firmware (test de paridad del protocolo contra los vectores dorados más compilación): `docker compose -f app/docker-compose.yml --profile firmware run --rm firmware-build`.

## Cableado y prueba con la placa real

Para cuando haya Arduino. Nada de esta sección se ha probado en hardware.

### Pines

| Señal | Pin del Arduino | Notas |
|---|---|---|
| Módulo EMG `SIG` | `A0` | Ver los avisos de alimentación más abajo. |
| Cordón elástico | `A1` | Divisor: 5 V -> resistencia de 10 kohm -> punto medio (`A1`) -> cordón -> GND. |
| MPU-6050 `SDA` | `A4` | I2C, dirección 0x68. |
| MPU-6050 `SCL` | `A5` | |
| MPU-6050 `VCC` / `GND` | 5 V (GY-521) / GND | |
| `A3` | sin conectar | Se deja flotante a propósito: su ruido alimenta el `boot_id`. |

Electrodos del EMG (tres, conectados al módulo EMG, no al Arduino): dos diferenciales sobre el vientre muscular, alineados con la dirección de las fibras, y uno de referencia en una zona ósea neutra.

### Seguridad

- El módulo EMG probablemente necesita **alimentación dual** (`+Vs` y `-Vs`) y su salida `SIG` puede superar los 5 V. Mide con un multímetro, en reposo y con una contracción máxima, y limita la señal a 5 V (y por encima de 0 V) antes de que llegue a `A0`: la entrada del ATmega328P no tolera más de Vcc ni tensiones negativas.
- La placa GY-521 acepta 5 V (lleva regulador); un chip MPU-6050 desnudo es de 3,3 V.
- No conectes electrodos a una persona hasta haber verificado la alimentación y el aislamiento del módulo.

### Paso a paso

1. Sube el sketch con el Arduino IDE: abre `firmware/spine_wearable/spine_wearable.ino`, placa Arduino Uno o Nano (en clones Nano elige el procesador "ATmega328P (Old Bootloader)"), elige el puerto y sube.
2. Abre el Monitor Serie a **250000 baud**. Debes ver un `H,...` una vez, `T,...` cada segundo, `D,E`/`D,I`/`D,S` cada 100 ms y `#imu ok` (o `#imu absent` si el MPU-6050 no responde).
3. **Cierra el Monitor Serie**: el puerto es exclusivo y el gateway no puede abrirlo mientras lo tenga el IDE.
4. Levanta el broker: `docker compose -f app/docker-compose.yml up -d mosquitto`.
5. Ejecuta el gateway nativo (ver arriba) con el puerto correcto.
6. Observa el bus con `mosquitto_sub` (ver Notas) o con un pequeño script de aiomqtt: `status` debe pasar a `ok` y deben llegar cada segundo chunks de fondo de `imu_l3` y `stretch_lumbar`.
7. Publica los comandos (JSON que valide contra `telemetry/command`) en `.../cmd`: `start_session` (chunks a 100 ms y aparece el EMG), `calibrate` de tipo `stretch` y `emg_mvc` (reposo para el primero, una contracción máxima para el segundo), `stop_session`. Comprueba los acks y que las calibraciones quedan retained.
8. Comprueba el cordón (resistencia en reposo y su cambio al estirar) y el IMU (en torno a 1 g en el eje vertical en reposo).

### Cerrar "EMG cruda o envolvente" (#13)

Con el módulo conectado, mide `SIG` con un multímetro (o mira `emg` en una sesión):

- Si `SIG` está en reposo en torno a 0 V y sube al contraer el músculo, el módulo entrega **envolvente**: deja `SPINE_GATEWAY_EMG_SIGNAL_KIND=envelope`.
- Si `SIG` oscila alrededor de una tensión de reposo, entrega señal **cruda**: pon `SPINE_GATEWAY_EMG_SIGNAL_KIND=raw` (y comprueba que la tensión de reposo es de verdad la media escala, que es lo que supone el gateway).

## Puntos abiertos

- EMG cruda o envolvente (`SPINE_GATEWAY_EMG_SIGNAL_KIND`), pendiente del módulo real.
- Alimentación del módulo EMG y rango real de `SIG`.
- `rest_length_mm` y la curva real del cordón (#12); hoy son un marcador de posición y una aproximación lineal.
- `calibrate` de tipo `imu` no está implementado.
- Un código de error propio para "no soportado": requiere cambiar el contrato (hoy se usa `invalid_command`).
- Autenticación y TLS del broker (#96).
- BLE queda fuera de alcance.
