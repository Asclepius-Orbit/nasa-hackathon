[English](README.md) · **Español** · [Català](README.ca.md)

# spine-backend (la app de a bordo)

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
  dispara `/assess`. Sin perfil, baseline o feature sets la evaluación se omite y se dice por qué. La
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

| Método | Ruta | Qué |
|---|---|---|
| GET | `/ui/crew/{astronaut_id}/devices` | Último estado de cada dispositivo |
| PUT/GET | `/ui/crew/{astronaut_id}/profile` | Perfil del astronauta |
| PUT/GET | `/ui/crew/{astronaut_id}/daily-context/{date}` | Contexto diario |
| POST | `/ui/crew/{astronaut_id}/self-reports` | Diario, ODI semanal o bandera roja; devuelve el informe y la nueva evaluación (o por qué se omitió). Repetir el mismo `report_id` es idempotente |
| GET | `/ui/crew/{astronaut_id}/self-reports/due` | Si toca el autoinforme diario y el ODI semanal; las banderas rojas siempre están disponibles |
| POST | `/ui/crew/{astronaut_id}/sessions` | Plan de la sesión (dispositivo, tipo, protocolo, streams, carga...) -> `start_session`. `mission_context` toma por defecto el contexto diario de hoy |
| GET | `/ui/sessions/{session_id}` | Sesión, estado (`starting`, `running`, `ended`, `processing`, `processed`, `processing_failed`, `rejected`) y feature set |
| POST | `/ui/sessions/{session_id}/reprocess` | Solo para `processing_failed` (409 en otro caso, 404 si no existe): reinicia los intentos, la pasa a `ended` y la procesa en segundo plano. 202 `{"session": id, "state": "ended"}`. Es la única forma de relanzar una sesión que falló de forma definitiva o antes de que se contaran los intentos |
| POST | `/ui/sessions/{session_id}/events` | `mark_event` |
| POST | `/ui/sessions/{session_id}/calibrations` | `calibrate` |
| POST | `/ui/sessions/{session_id}/stop` | `stop_session` con un motivo |
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
- Recursos: una evaluación, sesión (solo las que se llegaron a ejecutar), feature set de una sesión,
  autoinforme o baseline, y listas de evaluaciones, sesiones y autoinformes de una ventana.
- Las ventanas son inclusivas, RFC 3339 con offset; `to` anterior a `from` es `invalid_range`. Las listas
  van de más antiguo a más reciente, acotadas por `limit` (1-500, por defecto 100), conservando lo más
  reciente y marcando `truncated`.
- Un astronauta o entidad desconocidos dan `404 not_found`; los errores son problem details de `app/error`.
- Se prueba contra el contrato con `openapi_drift` y se fuzzea con Schemathesis.

Arrancarla sola: `uv run uvicorn --factory monitor.readonly:create_read_app --port 8002`.

## Arrancarla
```
uv run uvicorn --factory monitor.main:create_app --port 8000
```
La configuración son variables `SPINE_APP_*` (`settings.py`): `MQTT_HOST`, `MQTT_PORT`, `ENGINE_URL`,
`DB_PATH`, `ACK_TIMEOUT_S`, `ASSESS_WINDOW_DAYS`, `STORE_UNKNOWN_SESSION_CHUNKS`, `LIVE_NO_SIGNAL_S`, `LIVE_HEARTBEAT_S`, `LIVE_TICK_S`,
`WEEKLY_ODI_INTERVAL_DAYS`. En Docker Compose la base de datos vive en el volumen `backend_data`
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

## Tipos TypeScript para la UI
`app/ui/src/contracts/v1.ts` se genera desde `contracts/schemas/v1`. Ejecuta `pnpm run gen:types` en
`app/ui` después de cualquier cambio de schema; `pnpm run check:types` falla si el fichero está
desactualizado.

## Tests
`uv run pytest backend/tests` (desde `spine_monitor`). Ejecutan la app contra un broker MQTT en memoria
y contra el motor real por ASGI. Solo se sustituye el extractor de features del motor, porque el real
aún no está implementado.
