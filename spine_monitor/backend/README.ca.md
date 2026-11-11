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
  **Recuperació després d'un reinici**: en engegar, una comanda `sent` passada d'`ack_timeout_s` passa a
  `timeout`; a les altres se'ls rearma el temporitzador amb el temps que els queda. Una sessió `starting`
  el `start_session` de la qual va caducar, o que no té comanda pendent, passa a `rejected` amb
  `ack_timeout` (un `accepted` tardà encara la revifa). Una sessió que un procés mort va deixar en
  `processing` torna a `ended`, i tota sessió `ended` es processa en segon pla: l'engegada mai no espera
  el motor.
- **Crides al motor** (`analysis.py`): quan acaba una sessió, `/features`. Si és de pre-vol,
  `/baseline` amb tots els feature sets de pre-vol (una versió nova cada vegada). Després, `/assess` amb
  perfil, baseline, feature sets recents, autoinformes i context diari. Cada autoinforme també dispara
  `/assess`. Desar el perfil o el context diari, o una baseline nova, demana una reavaluació amb
  debounce per astronauta (`REASSESS_DEBOUNCE_S`, 5 s després de l'última petició; una ràfega en fa
  una sola, i un `/assess` immediat mentrestant la substitueix). Un bucle de fons (`assessment-refresh`,
  cada `REASSESS_TICK_S`, 1 h) la demana per a cada astronauta amb perfil l'última avaluació del qual
  falta o és més antiga que `STALE_ASSESSMENT_HOURS`; si no es pot avaluar, es reintenta al tick següent. Sense perfil, baseline o feature sets l'avaluació s'omet i es diu per què. L'absència mai
  no es converteix en un verd. Una fallada de `/features` perquè el motor no respon o contesta 5xx es
  reintenta amb backoff exponencial (de `PROCESSING_RETRY_MIN_S` a `PROCESSING_RETRY_MAX_S`), fins a
  `PROCESSING_MAX_ATTEMPTS` intents, comptats a SQLite (`processing_attempts`) perquè un reinici
  continuï el compte. Els 4xx, els errors de contracte i les sessions sense chunks són definitius
  (`processing_failed`).
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
| POST | `/ui/sessions/{session_id}/reprocess` | Només per a `processing_failed` (409 en un altre cas, 404 si no existeix): reinicia els intents, la passa a `ended` i la processa en segon pla. 202 `{"session": id, "state": "ended"}`. És l'única manera de rellançar una sessió que ha fallat de manera definitiva o abans que es comptessin els intents |
| POST | `/ui/sessions/{session_id}/events` | `mark_event` |
| POST | `/ui/sessions/{session_id}/calibrations` | `calibrate` |
| POST | `/ui/sessions/{session_id}/stop` | `stop_session` amb un motiu |
| GET | `/ui/sessions/{session_id}/reps` | Server-sent events amb el comptador de repeticions en viu (`ui-openapi.yaml`): `state` a cada canvi, `end` quan acaba la sessió |
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
`DB_PATH`, `ACK_TIMEOUT_S`, `ASSESS_WINDOW_DAYS`, `STORE_UNKNOWN_SESSION_CHUNKS`, `LIVE_NO_SIGNAL_S`, `LIVE_HEARTBEAT_S`, `LIVE_TICK_S`,
`WEEKLY_ODI_INTERVAL_DAYS`, `REASSESS_DEBOUNCE_S`, `REASSESS_TICK_S`. A Docker Compose la base de dades viu al volum `backend_data`
(`/data/spine_monitor.db`).

Reintents de l'anàlisi, també `SPINE_APP_*`:

| Variable | Per defecte | Significat |
|---|---|---|
| `PROCESSING_RETRY_MIN_S` | 30 | Espera abans del primer reintent d'una fallada transitòria de `/features`; es dobla a cada fallada. |
| `PROCESSING_RETRY_MAX_S` | 900 | Espera màxima; no menor que la mínima. |
| `PROCESSING_MAX_ATTEMPTS` | 5 | Intents abans que la sessió es quedi en `processing_failed`. |
| `PROCESSING_RETRY_TICK_S` | 5 | Cada quant el reintentador busca un backoff vençut. |

Enllaç amb terra: cada assessment encua el resum del seu dia UTC a SQLite (`ground_outbox`) i una tasca de fons l'envia, per un
transport simulat, mentre hi ha enllaç; `GET /ui/crew/{id}/houston` i `/outbox` el mostren. Un enviament fallit es reintenta amb un
backoff per entrada i sense límit d'intents; després d'un reinici segueix des de SQLite. Variables, també `SPINE_APP_*`; un valor incorrecte impedeix engegar:

| Variable | Per defecte | Significat |
|---|---|---|
| `GROUND_WINDOW_ANCHOR` | sense definir | Inici d'una finestra, RFC 3339 amb zona. Sense definir: no hi ha enllaç configurat, `ground_link` és null i no s'envia res. |
| `GROUND_WINDOW_PERIOD_MIN` | 90 | Minuts entre l'inici de dues finestres. |
| `GROUND_WINDOW_UP_MIN` | 10 | Minuts d'enllaç a cada finestra; menys que el període. |
| `GROUND_RETRY_MIN_S` | 1 | Espera després de la primera fallada d'una entrada; es dobla a cada fallada. |
| `GROUND_RETRY_MAX_S` | 300 | Espera màxima; no menor que la mínima. |
| `GROUND_TICK_S` | 1 | Cada quant l'emissor busca una finestra oberta o un backoff vençut. |
| `GROUND_SEND_TIMEOUT_S` | 30 | Espera màxima que terra respongui a un enviament; passada, l'enviament falla amb codi `timeout` i s'aplica backoff. |

## Tipus TypeScript per a la UI
`app/ui/src/contracts/v1.ts` es genera des de `contracts/schemas/v1`. Executa `pnpm run gen:types` a
`app/ui` després de qualsevol canvi de schema; `pnpm run check:types` falla si el fitxer no està al dia.

## Tests
`uv run pytest backend/tests` (des de `spine_monitor`). Executen l'app contra un broker MQTT en memòria
i contra el motor real per ASGI. Només se substitueix l'extractor de features del motor, perquè el real
encara no està implementat.
