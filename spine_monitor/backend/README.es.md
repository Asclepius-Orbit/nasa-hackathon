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
- **Llamadas al motor** (`analysis.py`): al terminar una sesión, `/features`. Si es pre-vuelo,
  `/baseline` con todos los feature sets pre-vuelo (una versión nueva cada vez). Después, `/assess` con
  perfil, baseline, feature sets recientes, autoinformes y contexto diario. Cada autoinforme también
  dispara `/assess`. Sin perfil, baseline o feature sets la evaluación se omite y se dice por qué. La
  ausencia nunca se convierte en un verde.
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
| POST | `/ui/sessions/{session_id}/events` | `mark_event` |
| POST | `/ui/sessions/{session_id}/calibrations` | `calibrate` |
| POST | `/ui/sessions/{session_id}/stop` | `stop_session` con un motivo |
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
`DB_PATH`, `ACK_TIMEOUT_S`, `ASSESS_WINDOW_DAYS`, `STORE_UNKNOWN_SESSION_CHUNKS`,
`WEEKLY_ODI_INTERVAL_DAYS`. En Docker Compose la base de datos vive en el volumen `backend_data`
(`/data/spine_monitor.db`).

## Tipos TypeScript para la UI
`app/ui/src/contracts/v1.ts` se genera desde `contracts/schemas/v1`. Ejecuta `npm run gen:types` en
`app/ui` después de cualquier cambio de schema; `npm run check:types` falla si el fichero está
desactualizado.

## Tests
`uv run pytest backend/tests` (desde `spine_monitor`). Ejecutan la app contra un broker MQTT en memoria
y contra el motor real por ASGI. Solo se sustituye el extractor de features del motor, porque el real
aún no está implementado.
