[English](README.md) · **Español** · [Català](README.ca.md)

# spine-physiology

Generador sintético de misión. Publica lo que publicaría el hardware real: `StreamChunk` v1 con `source: simulator` (EMG, IMU y cordón elástico), sesiones, calibraciones, estado del dispositivo, sincronización de reloj, autoinformes diarios, contexto diario, perfiles de astronauta y, además, la **verdad de terreno** (lo que realmente ocurrió) para poder evaluar el motor contra el simulador.

## Diseño

- **Núcleo puro.** `MissionGenerator` es un iterador perezoso de `Envelope`s (un mensaje de contrato más su topic, su indicador retained y el instante UTC `at`). No hace E/S.
- **Sinks.** `JsonlSink` lo escribe todo a disco; `MqttSink` publica solo los mensajes que tienen topic (chunks, estado del dispositivo, reloj, acks de comandos y calibraciones, estas retained). El gateway falso reutiliza el mismo núcleo en tiempo real.
- **Contratos.** Los payloads se construyen con los modelos de `spine_contracts` y se validan contra sus JSON Schemas; `source` es siempre `simulator`.

## Sin valores clínicos en el código

Todo parámetro numérico, fisiológico o técnico, es obligatorio y sale del YAML de misión: el código no tiene valores por defecto. Los campos opcionales del contrato se escriben como un `null` explícito. `configs/example-mission.yaml` es un **PLACEHOLDER**: números inventados para que el simulador funcione de extremo a extremo, no referencias clínicas. Los valores reales deben sustituirlo. Las horas del día van entre comillas (`"07:00"`) y los exponentes se escriben `1.0e-3` (PyYAML lee `1e-3` como texto); el cargador es estricto y falla en vez de convertir en silencio.

## Configuración

`load_config(path)` valida el YAML con Pydantic (las claves desconocidas se rechazan). Bloques de `configs/example-mission.yaml`:

| Bloque | Qué define |
|---|---|
| `seed`, `start_date`, `start_mission_day` | semilla maestra; fecha del día 0; día de misión del día 0 (lanzamiento = 0) |
| `segments` | tramos consecutivos (`days`, `phase`, `gravity_environment`): preflight 1 g, inflight microgravedad, postflight 1 g |
| `crew` | por astronauta: ids, `latent_multipliers`, `exercise_adherence`, línea base (longitud de reposo del cordón, MVC de EMG por stream) y el `AstronautProfile` |
| `device` | firmware, enlace, encendido y apagado diarios (UTC), batería, temperatura, intervalos de estado y de sincronización de reloj, streams con su colocación |
| `protocols` | protocolos de ejercicio (repeticiones, duraciones, %MVC, ROM, carga) y de control de columna |
| `schedule` | sesiones por día (`time_utc`, jitter, fases, `every_n_days`) y la ventana opcional de monitorización de fondo (`background.chunk_ms`, obligatorio: duración del chunk del stream de fondo de IMU + cordón, separada de `signals.chunk_ms`; el ejemplo usa 1000 ms) |
| `calibration` | calibraciones de cordón, MVC de EMG e IMU: periodicidad, ruido, puntos |
| `curves` | trayectoria típica de los cuatro latentes diarios por día de misión (puntos por tramos), `daily_sd` y `dose_drift_per_day` |
| `signals` | `chunk_ms` y la síntesis de EMG, IMU y cordón (frecuencias, ruido, filtros, ADC, histéresis...) |
| `faults` | `quality` (una entrada por flag de calidad) y `device` (pérdida de paquetes, reinicios, reloj) |
| `daily_context` | ejercicio planificado, contramedidas y medicación por fase |
| `self_report` | hora, modelo de NRS de dolor y rigidez, localizaciones del dolor, sueño, banderas rojas |

Los cuatro latentes diarios son `spine_elongation` (mm sobre la longitud de reposo preflight), `muscle_deconditioning`, `rom_motor_control` y `pain_stiffness` (0 = preflight, 1 = deterioro máximo modelado). Valor del día = multiplicador del astronauta x curva(día de misión) + deriva por dosis + N(0, `daily_sd`). Las curvas son la trayectoria típica *con* el ejercicio planificado; cada día la deriva suma `dose_drift_per_day x (1 - minutos reales / minutos planificados)`.

## Inicio rápido

Desde el workspace `spine_monitor`. El índice de día `d` es la fecha `start_date + d`; la misión de ejemplo tiene 14 días (0 a 13).

```python
from pathlib import Path
from spine_physiology import MissionGenerator, load_config
from spine_physiology.sinks.jsonl import write_jsonl

config = load_config("physiology/configs/example-mission.yaml")
gen = MissionGenerator(config)                 # semilla del YAML; MissionGenerator(config, seed=7) la sustituye

# Todo un astronauta en un día, regenerado por separado (mismos payloads que en la misión completa)
for env in gen.day_messages(5, "AST-01"):
    print(env.kind.value, env.topic, env.at)  # tipo, topic MQTT o None, instante UTC
    break

# A disco: una carpeta por astronauta y día (la misión completa es gen.iter_messages())
n = write_jsonl(gen.day_messages(5, "AST-01"), Path("out"), config=config, seed=gen.seed)
```

Publicar por MQTT (vale cualquier objeto con la firma de `aiomqtt.Client.publish`; QoS 1, retained para el estado del dispositivo y las calibraciones). Los objetos de dominio se saltan y no se cuentan:

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

`spine_physiology.sinks.pacing.paced` puede dosificar un flujo a tiempo real o a `speed` x tiempo real. Un solo día de la misión de ejemplo son unos 22 600 mensajes, casi todos chunks (ver Límites conocidos); para un fragmento más barato usa `gen.session_messages(ref)` (más abajo).

## Reproducibilidad

- Misma configuración y misma semilla dan los mismos bytes: el JSONL usa JSON compacto, orden de claves fijo, UTF-8 y ningún valor de reloj de pared.
- El azar sale de flujos independientes derivados por rama (astronauta, día, sesión, stream...), así que cambiar una rama no desplaza el resto.
- El dispositivo arranca limpio cada día, de modo que nada pasa de un día al siguiente salvo la deriva por dosis de los latentes, que se puede regenerar por día.
- `gen.day_messages(día, astronaut_id)` y `gen.session_messages(ref)` dan exactamente los mismos payloads, en el mismo orden relativo, que dentro de la ejecución completa de `iter_messages()`.
- Un día o una sesión regenerados por separado son autosuficientes: por defecto (`include_carried_calibrations=True`) la salida empieza con las calibraciones registradas antes que siguen vigentes y que ella cita (de días anteriores o, en una sesión, registradas fuera de esa sesión), con exactamente los mismos sobres que emitió la ejecución completa (ids, `created_at` y `day` originales). `include_carried_calibrations=False` da el fragmento exacto de la ejecución completa; `iter_messages()` nunca las repite. Con `write_jsonl` acaban en la carpeta del día en que se registraron.

## Tierra vs microgravedad (`resimulate`)

La misma sesión, regenerada con otra gravedad, da un par A/B exacto (misma semilla, misma agenda, mismos fallos; solo cambia la física):

```python
from spine_contracts import GravityEnvironment
from spine_physiology import SessionRef

ref = SessionRef("AST-01", 5, "lat_pulldown")        # astronauta, día, slot = `name` en la agenda
micro = list(gen.session_messages(ref))              # como está configurado (microgravedad en los días inflight)
earth = gen.resimulate(ref, GravityEnvironment.EARTH_1G)
```

`gen.sessions(día, astronaut_id)` lista los `SessionPlan` de un día (`plan.ref`, `session_id`, inicio y fin).

## Fallos

**Fallos de calidad** (`faults.quality`, una entrada por `QualityFlag`). Cada uno tiene una probabilidad por chunk de iniciar un episodio, un rango de duración, un `score` de calidad, una `amplitude` (relativa a la señal limpia del canal en ese chunk: RMS, o pico para `clipping`) y los `kinds` de stream a los que afecta:

| Flag | Qué hace a la señal |
|---|---|
| `clipping` | recorta a +-amplitude x pico |
| `lead_off` | sustituye la señal por ruido de amplitude x RMS |
| `motion_artifact` | añade una ráfaga de baja frecuencia; la banda la fijan `band_low_hz`, `band_high_hz` y `max_fraction_of_rate` |
| `powerline_noise` | añade un seno a `frequency_hz` |
| `low_snr` | añade ruido blanco |
| `saturated_adc` | cuentas clavadas al máximo del ADC (o al rango del canal) |
| `out_of_calibration` | añade un desplazamiento (offset) |
| `sensor_warmup` | añade una deriva lenta que decae (`decay_fraction` de la duración del episodio) |

En el cordón solo se alteran las `adc_counts` (enteros, recortados al ADC); `length_mm` se deriva de ellas y hereda su calidad. Cada chunk afectado lleva el flag y el score mínimo.

**Fallos de dispositivo** (`faults.device`):

- `packet_loss`: chunks perdidos en ráfagas. También consumen `seq`, así que el hueco se ve; el primer chunk tras una pérdida lleva `packet_gap`, y las pérdidas largas marcan el stream como detenido (stalled) en el estado del dispositivo.
- `reboot`: reinicios espontáneos (Poisson por hora) con tiempo offline; tras uno, `seq` y `t0_device_ms` vuelven a 0 con un `boot_id` nuevo.
- `clock`: deriva de reloj por arranque (ppm) y latencia de recepción; cuando el error desde la última sincronización supera el umbral, los chunks llevan `clock_uncertain`.

La verdad de terreno registra cada episodio inyectado, cada chunk perdido y cada reinicio.

## Estructura de la salida

```
out/
  manifest.json                    configuración, semilla, versiones del paquete y del contrato
  AST-01/
    profile.json                   AstronautProfile
    day-012/
      chunks.jsonl  sessions.jsonl  calibrations.jsonl  self_reports.jsonl
      daily_context.jsonl  device_status.jsonl  clock_sync.jsonl
      command_acks.jsonl  ground_truth.jsonl
```

Cada línea es `{"topic", "retained", "at", "payload"}` para los tipos con topic (chunks, estado, reloj, acks, calibraciones) y `{"at", "payload"}` para los objetos de dominio. `JsonlSink` abre los ficheros de forma perezosa con un tope de manejadores abiertos y escribe `manifest.json` en `close()` (también funciona como gestor de contexto). El manifiesto lleva `complete: false` si el sink se cerró por una excepción. Las sesiones que no caben entre el encendido, la sesión anterior y el apagado se descartan ese día.

`ground_truth.jsonl` no tiene schema de contrato; cada registro lleva `type`, `astronaut_id` y `day` (los de sesión también `session_id` y `slot`): `latent_state` (latentes diarios y la dosis que los produjo), `device_events` (arranques, reinicios, deriva), `quality_faults`, `lost_chunks` y `expected_features` (valores verdaderos de las features del catálogo calculados del modelo sin ruido de sensor ni fallos).

## Gateway falso

`spine-sim-gateway` se comporta en MQTT como el gateway real del wearable, con un dispositivo por astronauta del config. La configuración son variables de entorno (o un `.env`):

| Variable | Por defecto | Significado |
|---|---|---|
| `SPINE_SIM_CONFIG` | obligatoria | YAML de misión |
| `SPINE_SIM_SEED` | la del YAML | sustituye la semilla |
| `SPINE_SIM_MQTT_HOST` / `SPINE_SIM_MQTT_PORT` | `127.0.0.1` / `1883` | broker |
| `SPINE_SIM_MQTT_CLIENT_ID` | `spine-sim-gateway` | prefijo del client id; cada dispositivo se conecta como `<prefijo>-<device_id>` |
| `SPINE_SIM_SPEED` | obligatoria | velocidad del día de misión: 1 = tiempo real, 0 = día fijo |
| `SPINE_SIM_START_DAY` | obligatoria | índice de día de misión al arrancar (0 = primer día simulado) |
| `SPINE_SIM_MQTT_RECONNECT_INITIAL_S` | `1.0` | primera espera antes de reconectar con el broker |
| `SPINE_SIM_MQTT_RECONNECT_FACTOR` | `2.0` | multiplicador de la espera tras cada intento fallido (>= 1) |
| `SPINE_SIM_MQTT_RECONNECT_MAX_S` | `30.0` | tope de la espera (>= la inicial) |
| `SPINE_SIM_MQTT_USERNAME` | sin fijar | usuario del broker; `{device_id}` se cambia por el id de cada dispositivo (compose: `{device_id}`, así el simulador entra como `sim-01`, `sim-02`) |
| `SPINE_SIM_MQTT_PASSWORD` | sin fijar | contraseña del broker, la misma para todos los dispositivos del proceso; nunca se registra ni se muestra |
| `SPINE_SIM_MQTT_TLS` / `SPINE_SIM_MQTT_TLS_CA_FILE` | `false` / sin fijar | TLS al broker (listener 8883 de la LAN) y la CA que firmó su certificado (sin ella, las CA del sistema) |

```bash
SPINE_SIM_CONFIG=physiology/configs/example-mission.yaml SPINE_SIM_SPEED=1 SPINE_SIM_START_DAY=0 \
  uv run spine-sim-gateway
```

- Se suscribe al topic de comandos de cada dispositivo y responde en el topic de acks. Los comandos se validan contra el JSON Schema. Admite `start_session`, `stop_session`, `mark_event` y `calibrate`. Todo comando aceptado recibe `accepted` y luego `completed`; `start_session` se rechaza (`rejected`) con `busy`, `unknown_stream` o `device_offline`, y `stop_session` y `mark_event` con `no_session` cuando no hay nada que parar o marcar. `mark_event` no cambia la señal; `calibrate` devuelve un `calibration_id` y la calibración se publica en el bus justo antes del ack `completed`. Un comando reenviado (mismo `command_id`) recibe los mismos acks sin volver a ejecutarse; los comandos mal formados, demasiado grandes (> 16 KiB) o con duraciones absurdas (> 30 días) se rechazan como `invalid_command` y nunca paran el gateway.
- `DeviceStatus` y `ClockSync` retained al arrancar y de forma periódica, un Last Will con `status: offline` y el mismo `offline` al parar de forma limpia. Por eso hay **una conexión MQTT por dispositivo**: MQTT solo admite un Last Will por conexión.
- Las calibraciones se publican retained (QoS 1) en `asclepius/v1/crew/{astronaut_id}/device/{device_id}/calibration/{stream_id}`: la última vigente por stream, según `gen.calibration_in_force(astronaut_id, stream_id, día)` (el último `calibrate` o, si no hay, la calibración programada de la misión para ese día). Además del comando `calibrate`, antes de un chunk que cita la calibración vigente el gateway la republica si no es la que está retained en el bus, así un consumidor que llega tarde puede resolverla. Tras un `calibrate`, los chunks en vivo citan la calibración nueva desde el siguiente bloque de síntesis (como mucho un intervalo de estado del dispositivo después); el resto del bloque en curso aún cita la anterior, que nunca se republica después de la nueva, así que la calibración retained de un stream nunca vuelve atrás.
- Reconexión: si el broker rechaza o corta la conexión, el gateway espera con backoff exponencial (los ajustes `SPINE_SIM_MQTT_RECONNECT_*`) y se conecta de nuevo con el mismo estado (sesión, fondo, `seq`). En cada conexión se vuelve a suscribir al topic de comandos y republica el estado `ok` del dispositivo y el anclaje de reloj. Un publish fallido se reintenta en la conexión siguiente (sin hueco de `seq`; en QoS 1 puede haber duplicados). La síntesis pesada corre en un hilo de trabajo, así que el keepalive de MQTT no se bloquea. `last_seq` de `DeviceStatus` es el último `seq` publicado de cada stream.
- Si `schedule.background.enabled`, los streams de fondo de IMU y cordón funcionan siempre que no haya sesión (la ventana horaria diaria del config se ignora, para que las demos valgan a cualquier hora). Una sesión pausa el fondo (mismo `stream_id`, el `seq` continúa) y después se reanuda con el mismo `session_id`.
- Día de misión = `start_day` + días de reloj de pared transcurridos x `speed`, acotado a la misión. Una sesión nunca cambia de día a mitad.
- Cuando se agota la batería de la ventana diaria de encendido y no hay sesión, el dispositivo se "recarga": `boot_id` nuevo, `seq` y reloj a 0, batería llena.

## Docker

El servicio `simulator` está en el perfil `sim` de `app/docker-compose.yml` (necesita el broker, que arranca con él: `mosquitto` tiene un healthcheck y `simulator` lo espera con `condition: service_healthy`). Desde la raíz del repo:

```bash
sh app/mosquitto/setup.sh   # una vez: credenciales del broker en app/.env
docker compose -f app/docker-compose.yml --profile sim up --build
```

Ejecuta `spine-sim-gateway` con la misión de ejemplo (`SPINE_SIM_CONFIG=/app/configs/example-mission.yaml`), velocidad 1 y día de inicio 0. La imagen se construye con `spine_monitor/physiology/Dockerfile` (contexto: la raíz del repo) y se ejecuta con un usuario sin privilegios.

## Tests

```bash
uv run pytest physiology
```

Los tests que necesitan un broker real llevan el marcador `mosquitto` y se saltan si nada escucha en `127.0.0.1:1883`; el gate (`SPINE_REQUIRE_BROKER=1`) falla en su lugar. Corren contra el broker de compose, con usuarios y ACL (`sh app/mosquitto/setup.sh` y luego `docker compose -f app/docker-compose.yml up -d mosquitto`), y leen sus credenciales de `SPINE_TEST_MQTT_USERNAME` (por defecto `spine-itest`), `SPINE_TEST_MQTT_PASSWORD` y `SPINE_TEST_MQTT_DEVICE_PASSWORD` (el servicio `quality` las pasa desde `app/.env`; a mano: `SPINE_MQTT_ITEST_PASSWORD` y `SPINE_MQTT_ITEST_DEVICE_PASSWORD` de ese fichero). Además del gateway de punta a punta, comprueban que se rechazan los clientes anónimos y las contraseñas erróneas y que un dispositivo no puede publicar en los topics de otro ni leer sus comandos. El resto de tests del gateway usan un cliente MQTT falso y tiempo virtual.

## Límites conocidos

- Volumen del fondo: con `schedule.background.chunk_ms` a 1000 ms, cada hora de fondo son unos 3 600 chunks por stream (10 veces menos que con chunks de 100 ms); sigue creciendo con la ventana. La configuración de ejemplo limita el fondo a una hora al día.
- El cuestionario ODI semanal está aplazado: solo se genera el autoinforme diario.
- Sesiones, autoinformes, contexto diario, perfiles y verdad de terreno no tienen topic MQTT: solo existen en JSONL (los objetos de dominio nunca van al bus). Las calibraciones sí van al bus.
- Todos los números de la configuración de ejemplo son placeholders, no referencias clínicas.
