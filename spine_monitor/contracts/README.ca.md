[English](README.md) · [Español](README.es.md) · **Català**

# spine-contracts (v1, esborrany per a revisió)

Contractes de dades compartits per tots els components: gateway del wearable, broker MQTT, app a bord, motor i servidor MCP.
**Els JSON Schemas són la font de veritat.** Tota la resta es genera a partir d'ells o es comprova contra ells.

## Estructura
| Ruta | Què és |
|---|---|
| `schemas/v1/common.schema.json` | Definicions comunes: ids, enums, qualitat, col·locació |
| `schemas/v1/telemetry/` | Missatges MQTT: chunk d'stream, estat del dispositiu, comanda, ack de comanda, sincronització de rellotge. L'entitat `domain/calibration` també es publica al bus (vegeu Convencions) |
| `schemas/v1/domain/` | Entitats: sessió, calibratge, perfil de l'astronauta, autoinforme, context diari, conjunt de features, baseline, avaluació |
| `schemas/v1/engine/` | Cossos HTTP del motor: `/features`, `/baseline`, `/assess`, `/simulate`, errors |
| `schemas/v1/app/` | Cossos HTTP de l'API de només lectura de l'app: estat, històric, explicació, llistes d'avaluacions, sessions i autoinformes, errors; i les vistes de la UI de `ui-openapi.yaml` (`ui-*`) |
| `schemas/v1/agent/` | Cossos HTTP de l'API de xat de l'agent LLM (`agent-openapi.yaml`): la pregunta (`chat-request`) i els esdeveniments SSE de la resposta (`chat-event`) |
| `catalog/feature-catalog.json` | Cada mètrica derivada (feature) amb unitat, entrades, requisits, `risk_direction` i rangs. `catalog_version` 0.2.0: les direccions i els rangs són **provisional, pendiente de Aguado** (l'estat continua sent `proposed` i cada font de rang comença per "provisional, pendiente de Aguado") fins que Aguado els validi |
| `catalog/protocol-catalog.json` | Dades fixes de cada protocol (banda, sèries, repeticions, descansos, agafada, tempo, postura, ids de col·locació) que citen les vistes de la UI |
| `asyncapi.yaml` | Topics MQTT, direcció, QoS i retain (des del punt de vista del gateway) |
| `openapi.yaml` | API HTTP del motor |
| `app-openapi.yaml` | API HTTP de només lectura de l'app (només GET), la fa servir el servidor MCP |
| `ui-openapi.yaml` | API de vistes de l'app per a la UI d'escriptori (només GET): una pantalla per endpoint, codis i dades, sense prosa |
| `agent-openapi.yaml` | API de xat de l'agent LLM per a l'app d'escriptori: POST d'una pregunta, la resposta en streaming com a esdeveniments SSE |
| `examples/valid/` | Un exemple vàlid per missatge; són els mocks per a tothom |
| `src/spine_contracts/` | Paquet Python: `models/` generats, `messages` (unions), `topics`, `validation`, `openapi_drift` |

## Què mesura el sistema
- **EMG** (1 canal diferencial, mV, 1000 Hz): activitat muscular, per comparar Terra i microgravetat amb el mateix exercici estandarditzat.
- **IMU MPU-6050** (accel en g, gyro en deg/s, 100 Hz per defecte): moviment de l'esquena durant l'exercici.
- **Cordó d'estirament** (comptes ADC, ohm, longitud derivada en mm, 20 Hz): elongació de la columna.
- **Autoinforme**: dolor (NRS 0-10), localització, rigidesa, son, Oswestry setmanal, banderes vermelles.
- **Context**: fase de missió, entorn de gravetat, medicació, exercici i contramesures.
- **Perfil**: edat, sexe biològic, antropometria, història de columna i espacial, forma física.

Les dades pertanyen a una **sessió**: `exercise` (protocol estandarditzat, p. ex. estirada al pit dret, amb càrrega declarada) o `spine_check` (mesura passiva curta). Només són comparables les dades amb el mateix `protocol_id`, i Terra enfront d'espai es llegeix a través de `gravity_environment`.

### El protocol d'exercici estandarditzat
`lat_pulldown_standing_v1` (mateix id, valors fixats per Aguado a la issue #12):
- **Exercici:** estirada al pit dret amb banda elàstica, agafada en pronació, tempo moderat. Banda verda (resistència alta, 15-35 kg segons l'estirament; 25 kg nominals), 4 sèries de 15 repeticions (60 en total, uns 5 minuts) amb recuperacions molt curtes (`rest_between_sets_s`, 30 s nominals fins que Aguado ho confirmi). La sessió declara `planned_sets`, `reps_per_set` i `planned_reps` (el total, `planned_sets * reps_per_set`); els esdeveniments `set_start` / `set_end` (amb `set_index`) delimiten les sèries i `rep_index` compta les repeticions de tota la sessió.
- **EMG:** un stream per cada dorsal ample (`emg_lat_left`, `emg_lat_right`), elèctrodes separats uns 10 cm (`electrode_spacing_cm`) cobrint T11-L2 (`span`), terra (GND) a la cresta ilíaca (`ground.location`).
- **IMU:** `imu_t9`, a T9.
- **Cordó elàstic:** `stretch_spine`, regió `full_spine`, de C3-C4 a L5-S1 (uns 650 mm); les seves features són `stretch.full_spine.*`.
- **Spine checks:** `spine_length_rest_v1`, dret, just abans (`phase: pre`) i just després (`phase: post`) de l'exercici; `related_session_id` és la sessió d'exercici que emmarquen.

## Convencions
- Camps i descripcions en anglès, `snake_case`.
- **Unitats canòniques fixades per canal** (g, deg/s, mV, ohm, mm, cm, kg, Hz): no es converteix res en rebre.
- **Els payloads MQTT són chunks**: un stream, mostres consecutives, un array per canal, `seq` per detectar pèrdues. Cada stream té la seva pròpia freqüència.
- **Temps**: `t0_device_ms` (monòton) + `boot_id` al dispositiu; el gateway afegeix `received_at` (UTC) i publica àncores `clock-sync`. Tota la resta és RFC 3339 amb desfasament.
- **Qualitat**: score 0..1 més `flags` enumerades per canal i chunk, i indica qui l'ha avaluada.
- `source` és `device`, `simulator` o `replay`: una demo que barregi dades reals i simulades ho ha de dir.
- **Els calibratges viatgen pel bus**: `asclepius/v1/crew/{astronaut_id}/device/{device_id}/calibration/{stream_id}` (QoS 1, retained, un topic per stream, payload = `domain/calibration`). L'últim vigent queda retingut, així que un consumidor pot resoldre el `calibration_id` dels chunks de stretch. Ho cobreixen `topics.calibration`, `topics.parse_calibration` i `SUBSCRIBE_ALL_CALIBRATIONS`.
- Els ids són pseudònims (`AST-01`); cap dada personal viatja pel bus MQTT.
- Tot missatge porta `schema_version` (semver, major 1).

## Ús des de Python
```python
from spine_contracts import stream_chunk_adapter, dump, topics, validation

chunk = stream_chunk_adapter.validate_json(payload)      # EmgChunk | ImuChunk | StretchChunk
topic = topics.stream("AST-01", "wb-01", chunk.stream_id)
validation.validate("telemetry/stream-chunk", dump(chunk))  # regles completes, vegeu a sota
```
- Els models Pydantic comproven estructura, tipus i rangs. **No** apliquen les regles `if/then` dels schemas (una sessió d'exercici necessita `load_declared`, una longitud derivada del cordó necessita `calibration_id`, una escalada requerida necessita `urgency`). Crida `validation.validate` allà on entrin dades al sistema.
- Serialitza amb `dump(model)`: només emet els camps assignats, que és el que esperen els schemas.
- Regenera els models després de qualsevol canvi d'schema: `uv run python contracts/scripts/generate_models.py` (des de `spine_monitor`). Un test falla si estan desactualitzats. Mai no editis `models/` a mà.
- Executa les comprovacions: `uv run pytest` (schemas, exemples, mutacions que s'han de rebutjar, codi generat, topics, referències d'AsyncAPI/OpenAPI).

## API de només lectura de l'app
`app-openapi.yaml` és el que fa servir el servidor MCP; mai no toca la base de dades de l'app. Només `GET`: cap endpoint modifica dades.
- **Endpoints per eina** retornen la feina ja feta per a una eina MCP: `/crew/{astronaut_id}/status` (`get_current_status`), `/crew/{astronaut_id}/history?from&to` (`get_history`), `/assessments/{assessment_id}/explanation` (`explain_assessment`). `compare_periods` no té endpoint: el servidor MCP crida dues vegades `/history` i compara.
- **Endpoints de recurs** retornen una entitat o una llista: avaluacions, sessions (només metadades, mai chunks crus), el conjunt de features d'una sessió, autoinformes i baselines.
- Les llistes exigeixen `from` i `to` (RFC 3339 amb desfasament) i accepten `limit` (1-500, per defecte 100). Van de més antic a més recent; si hi ha més elements es conserven els més recents i `truncated` és `true`.
- El que falta és `null`, i `status.freshness` ho marca `stale: true`: l'absència mai no es presenta com a bona notícia. Els llindars de `stale` els decideix l'app.
- Els errors són problem details RFC 9457 (`app/error`), com al motor.
- L'app construeix aquesta API amb FastAPI. El contracte és la referència: compara el seu document generat amb `openapi_drift.diff(contract, app.openapi())`, que llista rutes i `operationId` afegits, trets o reanomenats.
- `from` és paraula reservada de Python: els models generats l'anomenen `from_` i conserven `from` com a àlies. `dump` emet àlies.

## Canviar un contracte
Edita l'schema, afegeix o actualitza el seu exemple, regenera, executa els tests i avisa els amos de les altres capes. Les ampliacions compatibles pugen la versió menor; qualsevol altra cosa, la major.

## Obert
- **Aguado:** validar el catàleg de features (ids, unitats, `risk_direction`, rangs i les seves fonts): els valors de `risk_direction` i els rangs de `catalog_version` 0.2.0 són provisionals i estan pendents d'Aguado (#38; l'índex de simetria esquerra-dreta, #106, no es toca); el conjunt reduït d'ítems de l'ODI; la recuperació entre sèries (30 s és un valor provisional), el procediment de MVC i l'estirament exacte de la banda; la mesura del cordó (uns 650 mm, PENDIENTE medición Aguado: la corba resistència-longitud és provisional fins a mesurar-la amb un òhmetre; no és lineal i triga a recuperar-se, així que la longitud surt d'una corba mesurada).
- **Polfer:** el mòdul EMG dona el senyal cru o una envolvent? (les features espectrals necessiten cru). Fer servir pins ADC1 de l'ESP32 (l'ADC2 no funciona amb Bluetooth/WiFi). El format wearable→gateway és lliure; el contracte comença a MQTT. El gateway real fa servir el protocol sèrie documentat a `spine_monitor/gateway/README.ca.md` i `firmware/README.ca.md`.
- **Freqüència de la IMU:** 100 Hz per defecte; 10 Hz es queda curta per a suavitat i velocitat de pic d'una repetició de ~2 s.
- Sortides de les eines MCP: `compare_periods` té el seu propi schema, `mcp/compare-periods` (el calcula el servidor amb dues crides a `/history`); les altres eines MCP reutilitzen els schemas `app` i `engine`.
- Els tipus TypeScript de la UI es generen d'aquests schemas a `app/ui/src/contracts/v1.ts` (`pnpm run gen:types` a `app/ui`; `pnpm run check:types` falla si no estan al dia). Regenera'ls després de qualsevol canvi de schema.
- Encara sense cobrir: informe a Houston (FHIR/DTN).
- Revisió: Andreea. Validació semàntica: Aguado.

## Limitacions conegudes
- En microgravetat l'acceleròmetre no veu la gravetat: les features de la IMU que depenen de l'orientació no són comparables amb la Terra; el catàleg prefereix les basades en el giroscopi.
- La llibreria Python `jsonschema` aplica `pattern` amb `re.search`, i allà `$` admet un salt de línia final; per això `topics` fa servir `fullmatch`.
- Els models generats fan servir tipus `constr(...)` perquè els elements de llista amb restriccions continuïn sent cadenes planes; els analitzadors de tipus estàtics poden marcar-los.
