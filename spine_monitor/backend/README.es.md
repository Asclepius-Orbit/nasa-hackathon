[English](README.md) · **Español** · [Català](README.ca.md)

# spine-backend (la app de a bordo)

El endpoint autenticado `POST /ui/crew/{astronaut_id}/sessions/{session_id}/stretch-prediction` usa la resistencia almacenada y la baseline terrestre de la misma persona, postura y protocolo. El cliente aporta `reference_length_mm` (distancia terrestre medida entre contactos), `elapsed_days`, `assumed_daily_change_mm` y `sensor_uncertainty_ohm`. Los datos ausentes o una postura incompatible producen un error. La respuesta es la predicción experimental del motor y `X-Baseline-Id` identifica la baseline; las alturas opcionales proceden del perfil. No es una evaluación clínica. La petición no guarda datos.

Con los contratos v1 la app es la dueña de la parte con estado: escucha el bus, guarda todo en
SQLite, envía los comandos que guían el protocolo y llama al motor, que no tiene estado.

## Qué hace
- **Ingesta del bus** (`ingest.py`): se suscribe con QoS 1 a todos los `topics.SUBSCRIBE_ALL_*`
  (streams, estado, acks, anclajes de reloj, calibraciones). Cada mensaje se valida con
  `spine_contracts.validation` (el JSON Schema completo, con las reglas `if/then` que Pydantic no
  recoge), y sus ids deben coincidir con los de su topic. Los mensajes inválidos van a
  `rejected_messages` (acotada); la ingesta nunca se para. Los chunks se deduplican por stream +
  `boot_id` + `seq`.
- **Almacenamiento** (`db.py`): sesiones, chunks, calibraciones, anclajes de reloj, estado de los
  dispositivos, comandos y acks, feature sets, baselines, evaluaciones, perfil, autoinformes y contexto
  diario. Cada entidad se guarda tal como la define su schema.
- **Comandos** (`control.py`): `start_session`, `stop_session`, `mark_event`, `calibrate`, publicados
  en `.../cmd` y seguidos por sus acks (`accepted` -> `completed`/`failed`, `rejected`, o `timeout` en
  local). Los acks actualizan la sesión (en curso, terminada, eventos, ids de calibración).
  **Recuperación tras reinicio**: al arrancar, un comando `sent` pasado de `ack_timeout_s` pasa a
  `timeout`; a los demás se les rearma el temporizador con el tiempo que les queda. Una sesión `starting`
  cuyo `start_session` caducó, o que no tiene comando pendiente, pasa a `rejected` con `ack_timeout` (un
  `accepted` tardío aún la revive). Una sesión que un proceso muerto dejó en `processing` vuelve a
  `ended`, y toda sesión `ended` se procesa en segundo plano: el arranque nunca espera al motor.
- **Llamadas al motor** (`analysis.py`): al terminar una sesión, `/features`. Si es pre-vuelo,
  `/baseline` con todos los feature sets pre-vuelo (una versión nueva cada vez). Después, `/assess` con
  perfil, baseline, feature sets recientes, autoinformes y contexto diario. Cada autoinforme también
  dispara `/assess`. Guardar el perfil o el contexto diario, o una baseline nueva, pide una reevaluación
  con debounce por astronauta (`REASSESS_DEBOUNCE_S`, 5 s tras la última petición; una ráfaga da una sola,
  y un `/assess` inmediato entretanto la sustituye). Un bucle de fondo (`assessment-refresh`, cada
  `REASSESS_TICK_S`, 1 h) la pide para cada astronauta con perfil cuya última evaluación falta o es más
  vieja que `STALE_ASSESSMENT_HOURS`; si no se puede evaluar, se reintenta en el siguiente tick. Sin perfil, baseline o feature sets la evaluación se omite y se dice por qué. La
  ausencia nunca se convierte en un verde. Un fallo de `/features` porque el motor no responde o contesta
  5xx se reintenta con backoff exponencial (de `PROCESSING_RETRY_MIN_S` a `PROCESSING_RETRY_MAX_S`), hasta
  `PROCESSING_MAX_ATTEMPTS` intentos, contados en SQLite (`processing_attempts`) para que un reinicio siga
  con la cuenta. Los 4xx, los errores de contrato y las sesiones sin chunks son definitivos
  (`processing_failed`).
- **Ids seudónimos**: solo ids del tipo `AST-01`. Los comandos solo llevan los campos del schema (que
  no admite otros). El perfil es un dato personal y nunca va por el bus.

## API de la UI (`/ui`, `api.py`)
Lado de escritura para la UI. Separada de la API de solo lectura de `contracts/app-openapi.yaml` que
usa el servidor MCP (ver más abajo). La app rellena ids, `schema_version`, instantes y `source`, y valida la entidad
contra su schema. Los errores son problem details de `app/error`.

Toda petición a `/ui` necesita `Authorization: Bearer <token>` (si no, 401) y, cuando trata de un astronauta (su ruta lo nombra,
o nombra una sesión o un comando suyos), `X-Astronaut-Id` con el astronauta desbloqueado en la UI: otro distinto es un 403 con
problem details. `GET /ui/crew` y `PUT /ui/crew/{id}` solo necesitan el token. El token es `SPINE_APP_UI_TOKEN`, o el contenido
de `SPINE_APP_UI_TOKEN_FILE` (por defecto `ui-token` junto a la base de datos), que se genera ahí con permisos solo del dueño
en el primer arranque sin token. `/health` y la API de solo lectura no necesitan token.

| Método | Ruta | Qué |
|---|---|---|
| PUT | `/ui/crew/{astronaut_id}` | Registra un id que asignó la UI (sin cuerpo: nombres y PIN se quedan en la UI). 201 si es nuevo, 200 si ya estaba |
| GET | `/ui/crew` | Ids que conoce el backend (registrados o con datos), si cada uno tiene perfil y baseline, y cuántas sesiones (`ui-crew`, `ui-openapi.yaml`) |
| GET | `/ui/crew/{astronaut_id}/devices` | Último estado de cada dispositivo |
| POST/GET | `/ui/crew/{astronaut_id}/contacts` | Llamadas al oficial médico de a bordo (`domain/medical-contact`: quién, cuándo, nota opcional). Cada una vuelve a encolar el resumen de tierra de su día, que lleva el código y el instante, nunca la nota |
| PUT/GET | `/ui/crew/{astronaut_id}/profile` | Perfil del astronauta |
| PUT/GET | `/ui/crew/{astronaut_id}/daily-context/{date}` | Contexto diario |
| POST | `/ui/crew/{astronaut_id}/self-reports` | Diario, ODI semanal o bandera roja (con `other_symptoms`: `very_severe_pain`, `other`, aparte de las banderas rojas hasta que Aguado decida cómo se trian); devuelve el informe y la nueva evaluación (o por qué se omitió). Repetir el mismo `report_id` es idempotente |
| GET | `/ui/crew/{astronaut_id}/self-reports/due` | Si toca el autoinforme diario y el ODI semanal; las banderas rojas siempre están disponibles. El Oswestry se guarda y se muestra (pantalla Oswestry, historial) pero no puntúa riesgo: el motor, el modelo de riesgo y las reglas lo ignoran. Si entra en el resumen de Houston está pendiente (#85); `ground-summary` no tiene campo de ODI por ahora |
| POST | `/ui/crew/{astronaut_id}/sessions` | Plan de la sesión (dispositivo, tipo, protocolo, streams, carga...) -> `start_session`. `mission_context` toma por defecto el contexto diario de hoy |
| GET | `/ui/sessions/{session_id}` | Sesión, estado (`starting`, `running`, `ended`, `processing`, `processed`, `processing_failed`, `rejected`) y feature set |
| POST | `/ui/sessions/{session_id}/reprocess` | Solo para `processing_failed` (409 en otro caso, 404 si no existe): reinicia los intentos, la pasa a `ended` y la procesa en segundo plano. 202 `{"session": id, "state": "ended"}`. Es la única forma de relanzar una sesión que falló de forma definitiva o antes de que se contaran los intentos |
| POST | `/ui/sessions/{session_id}/events` | `mark_event` |
| POST | `/ui/sessions/{session_id}/calibrations` | `calibrate` |
| POST | `/ui/sessions/{session_id}/stop` | `stop_session` con un motivo. `aborted_pain` admite `rep_index` opcional (422 con otro motivo): la sesión recibe un evento `pain_reported` con la repetición y el instante, el resumen de tierra del día lo muestra (`pain_stop`) y se pide un /assess |
| GET | `/ui/sessions/{session_id}/reps` | Server-sent events con el contador de repeticiones en vivo (`ui-openapi.yaml`): `state` en cada cambio, `end` al terminar la sesión |
| GET | `/ui/commands/{command_id}` | Estado del comando y sus acks (la UI lo consulta para pasar al siguiente paso) |
| GET | `/health` | Vida, conexión MQTT y contadores de mensajes |

## API de solo lectura para el servidor MCP (`readonly.py`)
`contracts/app-openapi.yaml`, servida por una app FastAPI propia en el puerto 8002 (servicio `read-api`
de compose), así el servidor MCP nunca llega a los endpoints de escritura `/ui`. Abre la misma SQLite con
`PRAGMA query_only`: solo `GET`, y la conexión no puede escribir.

- `/crew/{astronaut_id}/status` (`get_current_status`): última evaluación, sesión y autoinforme,
  dispositivos, baseline activa y frescura. Lo que falta es `null` con `stale: true`; los umbrales son
  `SPINE_APP_STALE_*_HOURS`.
- `/crew/{astronaut_id}/history?from&to&limit` (`get_history`, y dos veces para `compare_periods`):
  feature sets por el inicio de su sesión, evaluaciones por `as_of`.
- `/assessments/{assessment_id}/explanation` (`explain_assessment`): la evaluación con sus entradas de
  baseline (solo de los drivers), feature sets y autoinformes resueltos; lo que ya no existe va en
  `unresolved`.
- `/crew/{astronaut_id}/assess-base` (`simulate_scenario`): la petición /assess que la app enviaría ahora (perfil,
  último baseline, y feature sets, autoinformes y contexto diario de los últimos `assess_window_days`, con
  `as_of` = el reloj de la app), construida por el mismo código que la valoración real y nunca enviada. Un
  astronauta desconocido, o sin datos para construirla, es `404 not_found` con `detail` `no_profile`, `no_baseline`
  o `no_feature_sets`.
- Recursos: una evaluación, sesión (solo las que se llegaron a ejecutar), feature set de una sesión,
  autoinforme o baseline, y listas de evaluaciones, sesiones y autoinformes de una ventana.
- Las ventanas son inclusivas, RFC 3339 con offset; `to` anterior a `from` es `invalid_range`. Las listas
  van de más antiguo a más reciente, acotadas por `limit` (1-500, por defecto 100), conservando lo más
  reciente y marcando `truncated`.
- Un astronauta o entidad desconocidos dan `404 not_found`; los errores son problem details de `app/error`.
- Se prueba contra el contrato con `openapi_drift` y se fuzzea con Schemathesis.

Arrancarla sola: `uv run uvicorn --factory monitor.readonly:create_read_app --port 8002`.

## Base de datos: migraciones, copias, retención y exportación

- El esquema está versionado (`PRAGMA user_version`, `STORE_SCHEMA_VERSION` en `db.py`). Un fichero más antiguo se copia
  primero con la API de backup de sqlite3 a `backups/<nombre>.v<N>.<instante>.db` y luego se migra paso a paso
  (`MIGRATIONS[n]`: n -> n+1) en una transacción junto con el cambio de versión: una migración que falla lo deja como estaba.
  Un fichero más nuevo, o ajeno, se rechaza.
- WAL. La API de solo lectura (conexión query-only) nunca escribe ni migra: rechaza un fichero que el backend aún no ha
  migrado y lee lo que el backend escribe mientras ambos están abiertos.
- Cada `SPINE_APP_BACKUP_INTERVAL_S` (por defecto un día) va una copia a `SPINE_APP_BACKUP_DIR` (por defecto `backups/` junto a
  la base de datos, o sea en el volumen `backend_data`), y se guardan las `SPINE_APP_BACKUP_KEEP` (7) más recientes. Tras esa
  copia se borran los chunks crudos de las sesiones `processed` que empezaron hace más de `SPINE_APP_CHUNK_RETENTION_DAYS` (30;
  con `=null` se guardan siempre) y la sesión queda marcada (`chunks_purged_at`). Feature sets, baselines, evaluaciones y
  autoinformes no se borran nunca.
- `GET /ui/crew/{astronaut_id}/export`: todo lo guardado del astronauta (perfil, contextos diarios, sesiones con su estado,
  feature sets, baselines, evaluaciones, autoinformes, contactos médicos, estados de dispositivos) salvo los chunks crudos.

## Arrancarla
```
uv run uvicorn --factory monitor.main:create_app --port 8000
```
La configuración son variables `SPINE_APP_*` (`settings.py`): `MQTT_HOST`, `MQTT_PORT`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_TLS`, `MQTT_TLS_CA_FILE`, `ENGINE_URL`,
`DB_PATH`, `ACK_TIMEOUT_S`, `ASSESS_WINDOW_DAYS`, `STORE_UNKNOWN_SESSION_CHUNKS`, `LIVE_NO_SIGNAL_S`, `LIVE_HEARTBEAT_S`, `LIVE_TICK_S`,
`WEEKLY_ODI_INTERVAL_DAYS`, `REASSESS_DEBOUNCE_S`, `REASSESS_TICK_S`. En Docker Compose la base de datos vive en el volumen `backend_data`
(`/data/spine_monitor.db`).

Reintentos del análisis, también `SPINE_APP_*`:

| Variable | Por defecto | Significado |
|---|---|---|
| `PROCESSING_RETRY_MIN_S` | 30 | Espera antes del primer reintento de un fallo transitorio de `/features`; se dobla en cada fallo. |
| `PROCESSING_RETRY_MAX_S` | 900 | Espera máxima; no menor que la mínima. |
| `PROCESSING_MAX_ATTEMPTS` | 5 | Intentos antes de que la sesión se quede en `processing_failed`. |
| `PROCESSING_RETRY_TICK_S` | 5 | Cada cuánto el reintentador busca un backoff vencido. |

Enlace con tierra: cada assessment encola el resumen de su día UTC en SQLite (`ground_outbox`) y una tarea de fondo lo envía, por un
transporte simulado, mientras hay enlace; `GET /ui/crew/{id}/houston` y `/outbox` lo muestran. Un envío fallido se reintenta con un
backoff por entrada y sin límite de intentos; tras un reinicio sigue desde SQLite. Variables, también `SPINE_APP_*`; un valor incorrecto impide arrancar:

| Variable | Por defecto | Significado |
|---|---|---|
| `GROUND_WINDOW_ANCHOR` | sin definir | Inicio de una ventana, RFC 3339 con zona. Sin definir: no hay enlace configurado, `ground_link` es null y no se envía nada. |
| `GROUND_WINDOW_PERIOD_MIN` | 90 | Minutos entre el inicio de dos ventanas. |
| `GROUND_WINDOW_UP_MIN` | 10 | Minutos de enlace en cada ventana; menos que el periodo. |
| `GROUND_RETRY_MIN_S` | 1 | Espera tras el primer fallo de una entrada; se dobla en cada fallo. |
| `GROUND_RETRY_MAX_S` | 300 | Espera máxima; no menor que la mínima. |
| `GROUND_TICK_S` | 1 | Cada cuánto el emisor busca una ventana abierta o un backoff vencido. |
| `GROUND_SEND_TIMEOUT_S` | 30 | Espera máxima a que tierra responda a un envío; pasada, el envío falla con código `timeout` y se aplica backoff. |

## Broker MQTT: usuarios, ACL y acceso por la LAN
El Mosquitto de a bordo (`app/docker-compose.yml`) rechaza los clientes anónimos: cada cliente se identifica y solo llega a
los topics de su ACL (`app/mosquitto/acl.template` y `acl-device.template`, a partir de los topics de `contracts/asyncapi.yaml`).
En el repo no hay ninguna credencial. Antes del primer `docker compose`, desde la raíz del repo (Linux, macOS o Git Bash en
Windows; solo necesita docker):
```bash
sh app/mosquitto/setup.sh
```
Escribe contraseñas aleatorias en `app/.env` y genera `app/mosquitto/auth/passwd` (con el hash de `mosquitto_passwd` dentro
de la imagen del broker) y `app/mosquitto/auth/acl`; todos están en `.gitignore`, y todo comando de compose falla con
`run sh app/mosquitto/setup.sh` mientras falten. Volver a ejecutarlo conserva las contraseñas; `--force` las rota. Tras
cualquier cambio, recrea el broker (`docker compose -f app/docker-compose.yml up -d --force-recreate mosquitto`) y, tras una
rotación, los clientes. La CI genera unas desechables en cada ejecución.

| Usuario | Quién | Permisos |
|---|---|---|
| `spine-app` | backend | lee `stream/+`, `status`, `ack`, `clock` y `calibration/+` de todos los dispositivos; escribe todo `cmd` |
| `spine-health` | healthcheck de compose | lee `$SYS/broker/uptime` |
| `spine-itest` | tests `mosquitto` | los mismos que `spine-app` |
| `<device_id>` | un dispositivo: `sim-01`, `sim-02` (simulador), `itest-01`, `itest-02` (tests), los reales | solo bajo `asclepius/v1/crew/<astronaut_id>/device/<device_id>/`: escribe `stream/+`, `status`, `ack`, `clock`, `calibration/+`; lee `cmd` |

Un publish que la ACL deniega se descarta en silencio, y una suscripción denegada no recibe nada. La configuración del
backend es `SPINE_APP_MQTT_USERNAME`, `SPINE_APP_MQTT_PASSWORD` (nunca se registra ni se muestra), `SPINE_APP_MQTT_TLS`
(`true` para conectar por TLS) y `SPINE_APP_MQTT_TLS_CA_FILE` (la CA que firmó el certificado del broker; sin ella, las CA
del sistema). Compose fija las dos primeras desde `app/.env`.

### Dar de alta un dispositivo
```bash
sh app/mosquitto/setup.sh --device AST-03:wb-01
docker compose -f app/docker-compose.yml up -d --force-recreate mosquitto
```
El usuario es el device id (`wb-01`) y su contraseña es `SPINE_MQTT_DEVICE_PASSWORD_WB_01` de `app/.env`; configura ambos en
el gateway del dispositivo (en el gateway simulado, `SPINE_SIM_MQTT_USERNAME` y `SPINE_SIM_MQTT_PASSWORD`). Los ids siguen
`spine_contracts.topics` (`AST-NN`, `[a-z0-9][a-z0-9_-]{1,31}`); con solo `wb-01` el dispositivo puede publicar para
cualquier astronauta. Para darlo de baja, bórralo de `SPINE_MQTT_DEVICES` junto con la línea de su contraseña en `app/.env`,
vuelve a ejecutar el script y recrea el broker.

### Acceso por la LAN con TLS
El puerto 1883 (MQTT sin cifrar) solo se publica en `127.0.0.1`. Para un gateway en otra máquina, `app/docker-compose.lan.yml`
añade un listener TLS en el 8883, con los mismos usuarios y ACL, publicado en una dirección del host y con los certificados
montados desde una carpeta fuera del repo (nunca se commitean):
```bash
export SPINE_MQTT_TLS_DIR="$HOME/spine-mqtt-certs" SPINE_MQTT_LAN_ADDRESS=192.168.1.20
docker compose -f app/docker-compose.yml -f app/docker-compose.lan.yml up -d mosquitto
```
La carpeta contiene `ca.crt`, `server.crt` y `server.key`. Una CA privada y un certificado para esa dirección:
```bash
mkdir -p "$SPINE_MQTT_TLS_DIR" && cd "$SPINE_MQTT_TLS_DIR"
openssl req -x509 -newkey rsa:3072 -nodes -days 365 -keyout ca.key -out ca.crt -subj "/CN=spine-mqtt-ca"
openssl req -newkey rsa:3072 -nodes -keyout server.key -out server.csr -subj "/CN=spine-broker"
printf 'subjectAltName=IP:%s\n' "$SPINE_MQTT_LAN_ADDRESS" > san.ext
openssl x509 -req -in server.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 365 -extfile san.ext -out server.crt
chmod 600 ca.key server.key
```
Saca `ca.key` de la estación una vez firmado el certificado. El gateway se conecta a esa dirección en el puerto 8883 con TLS
y `ca.crt` como CA (`SPINE_SIM_MQTT_TLS=true`, `SPINE_SIM_MQTT_TLS_CA_FILE=ca.crt`).

## Tipos TypeScript para la UI
`app/ui/src/contracts/v1.ts` se genera desde `contracts/schemas/v1`. Ejecuta `pnpm run gen:types` en
`app/ui` después de cualquier cambio de schema; `pnpm run check:types` falla si el fichero está
desactualizado.

## Tests
`uv run pytest backend/tests` (desde `spine_monitor`). Ejecutan la app contra un broker MQTT en memoria
y contra el motor real por ASGI. Solo se sustituye el extractor de features del motor, porque el real
aún no está implementado.
