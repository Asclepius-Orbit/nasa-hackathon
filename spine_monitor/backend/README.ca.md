[English](README.md) · [Español](README.es.md) · **Català**

# spine-backend (l'app de bord)

Amb els contractes v1 l'app és la propietària de la part amb estat: escolta el bus, ho desa tot a
SQLite, envia les comandes que guien el protocol i crida el motor, que no té estat.

## Què fa
- **Ingesta del bus** (`ingest.py`): se subscriu amb QoS 1 a tots els `topics.SUBSCRIBE_ALL_*`
  (streams, estat, acks, àncores de rellotge, calibratges). Cada missatge es valida amb
  `spine_contracts.validation` (el JSON Schema complet, amb les regles `if/then` que Pydantic no
  recull), i els seus ids han de coincidir amb els del seu topic. Els missatges invàlids van a
  `rejected_messages` (acotada); la ingesta no s'atura mai. Els chunks es dedupliquen per stream +
  `boot_id` + `seq`.
- **Emmagatzematge** (`db.py`): sessions, chunks, calibratges, àncores de rellotge, estat dels
  dispositius, comandes i acks, feature sets, baselines, avaluacions, perfil, autoinformes i context
  diari. Cada entitat es desa tal com la defineix el seu schema.
- **Comandes** (`control.py`): `start_session`, `stop_session`, `mark_event`, `calibrate`, publicades
  a `.../cmd` i seguides pels seus acks (`accepted` -> `completed`/`failed`, `rejected`, o `timeout` en
  local). Els acks actualitzen la sessió (en curs, acabada, esdeveniments, ids de calibratge).
- **Crides al motor** (`analysis.py`): quan acaba una sessió, `/features`. Si és de pre-vol,
  `/baseline` amb tots els feature sets de pre-vol (una versió nova cada vegada). Després, `/assess` amb
  perfil, baseline, feature sets recents, autoinformes i context diari. Cada autoinforme també dispara
  `/assess`. Sense perfil, baseline o feature sets l'avaluació s'omet i es diu per què. L'absència mai
  no es converteix en un verd.
- **Ids pseudònims**: només ids del tipus `AST-01`. Les comandes només porten els camps del schema
  (que no n'admet d'altres). El perfil és una dada personal i mai no va pel bus.

## API de la UI (`/ui`, `api.py`)
Costat d'escriptura per a la UI. Separada de l'API de només lectura de `contracts/app-openapi.yaml`
que fa servir el servidor MCP (vegeu més avall). L'app omple ids, `schema_version`, instants i `source`, i valida
l'entitat contra el seu schema. Els errors són problem details d'`app/error`.

| Mètode | Ruta | Què |
|---|---|---|
| GET | `/ui/crew/{astronaut_id}/devices` | Últim estat de cada dispositiu |
| PUT/GET | `/ui/crew/{astronaut_id}/profile` | Perfil de l'astronauta |
| PUT/GET | `/ui/crew/{astronaut_id}/daily-context/{date}` | Context diari |
| POST | `/ui/crew/{astronaut_id}/self-reports` | Diari, ODI setmanal o bandera vermella; retorna l'informe i la nova avaluació (o per què s'ha omès). Repetir el mateix `report_id` és idempotent |
| GET | `/ui/crew/{astronaut_id}/self-reports/due` | Si toca l'autoinforme diari i l'ODI setmanal; les banderes vermelles sempre estan disponibles |
| POST | `/ui/crew/{astronaut_id}/sessions` | Pla de la sessió (dispositiu, tipus, protocol, streams, càrrega...) -> `start_session`. `mission_context` agafa per defecte el context diari d'avui |
| GET | `/ui/sessions/{session_id}` | Sessió, estat (`starting`, `running`, `ended`, `processing`, `processed`, `processing_failed`, `rejected`) i feature set |
| POST | `/ui/sessions/{session_id}/events` | `mark_event` |
| POST | `/ui/sessions/{session_id}/calibrations` | `calibrate` |
| POST | `/ui/sessions/{session_id}/stop` | `stop_session` amb un motiu |
| GET | `/ui/commands/{command_id}` | Estat de la comanda i els seus acks (la UI el consulta per passar al pas següent) |
| GET | `/health` | Vida, connexió MQTT i comptadors de missatges |

## API de només lectura per al servidor MCP (`readonly.py`)
`contracts/app-openapi.yaml`, servida per una app FastAPI pròpia al port 8002 (servei `read-api` de
compose), així el servidor MCP mai no arriba als endpoints d'escriptura `/ui`. Obre la mateixa SQLite amb
`PRAGMA query_only`: només `GET`, i la connexió no pot escriure.

- `/crew/{astronaut_id}/status` (`get_current_status`): última avaluació, sessió i autoinforme,
  dispositius, baseline activa i frescor. El que falta és `null` amb `stale: true`; els llindars són
  `SPINE_APP_STALE_*_HOURS`.
- `/crew/{astronaut_id}/history?from&to&limit` (`get_history`, i dues vegades per a `compare_periods`):
  feature sets per l'inici de la seva sessió, avaluacions per `as_of`.
- `/assessments/{assessment_id}/explanation` (`explain_assessment`): l'avaluació amb les seves entrades de
  baseline (només dels drivers), feature sets i autoinformes resolts; el que ja no existeix va a
  `unresolved`.
- Recursos: una avaluació, sessió (només les que es van executar), feature set d'una sessió, autoinforme o
  baseline, i llistes d'avaluacions, sessions i autoinformes d'una finestra.
- Les finestres són inclusives, RFC 3339 amb offset; `to` anterior a `from` és `invalid_range`. Les
  llistes van de més antic a més recent, acotades per `limit` (1-500, per defecte 100), conservant el més
  recent i marcant `truncated`.
- Un astronauta o entitat desconeguts donen `404 not_found`; els errors són problem details d'`app/error`.
- Es prova contra el contracte amb `openapi_drift` i es fuzzeja amb Schemathesis.

Engegar-la sola: `uv run uvicorn --factory monitor.readonly:create_read_app --port 8002`.

## Engegar-la
```
uv run uvicorn --factory monitor.main:create_app --port 8000
```
La configuració són variables `SPINE_APP_*` (`settings.py`): `MQTT_HOST`, `MQTT_PORT`, `ENGINE_URL`,
`DB_PATH`, `ACK_TIMEOUT_S`, `ASSESS_WINDOW_DAYS`, `STORE_UNKNOWN_SESSION_CHUNKS`,
`WEEKLY_ODI_INTERVAL_DAYS`. A Docker Compose la base de dades viu al volum `backend_data`
(`/data/spine_monitor.db`).

## Tipus TypeScript per a la UI
`app/ui/src/contracts/v1.ts` es genera des de `contracts/schemas/v1`. Executa `pnpm run gen:types` a
`app/ui` després de qualsevol canvi de schema; `pnpm run check:types` falla si el fitxer no està al dia.

## Tests
`uv run pytest backend/tests` (des de `spine_monitor`). Executen l'app contra un broker MQTT en memòria
i contra el motor real per ASGI. Només se substitueix l'extractor de features del motor, perquè el real
encara no està implementat.
