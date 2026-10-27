[English](README.md) · [Español](README.es.md) · **Català**

# spine-contracts (v1, esborrany per a revisió)

Contractes de dades compartits per tots els components: gateway del wearable, broker MQTT, app a bord, motor i servidor MCP.
**Els JSON Schemas són la font de veritat.** Tota la resta es genera a partir d'ells o es comprova contra ells.

## Estructura
| Ruta | Què és |
|---|---|
| `schemas/v1/common.schema.json` | Definicions comunes: ids, enums, qualitat, col·locació |
| `schemas/v1/telemetry/` | Missatges MQTT: chunk d'stream, estat del dispositiu, comanda, ack de comanda, sincronització de rellotge |
| `schemas/v1/domain/` | Entitats: sessió, calibratge, perfil de l'astronauta, autoinforme, context diari, conjunt de features, baseline, avaluació |
| `schemas/v1/engine/` | Cossos HTTP del motor: `/features`, `/baseline`, `/assess`, `/simulate`, errors |
| `catalog/feature-catalog.json` | Cada mètrica derivada (feature) amb unitat, entrades i requisits; els rangs són d'Aguado |
| `asyncapi.yaml` | Topics MQTT, direcció, QoS i retain (des del punt de vista del gateway) |
| `openapi.yaml` | API HTTP del motor |
| `examples/valid/` | Un exemple vàlid per missatge; són els mocks per a tothom |
| `src/spine_contracts/` | Paquet Python: `models/` generats, `messages` (unions), `topics`, `validation` |

## Què mesura el sistema
- **EMG** (1 canal diferencial, mV, 1000 Hz): activitat muscular, per comparar Terra i microgravetat amb el mateix exercici estandarditzat.
- **IMU MPU-6050** (accel en g, gyro en deg/s, 100 Hz per defecte): moviment de l'esquena durant l'exercici.
- **Cordó d'estirament** (comptes ADC, ohm, longitud derivada en mm, 20 Hz): elongació de la columna.
- **Autoinforme**: dolor (NRS 0-10), localització, rigidesa, son, Oswestry setmanal, banderes vermelles.
- **Context**: fase de missió, entorn de gravetat, medicació, exercici i contramesures.
- **Perfil**: edat, sexe biològic, antropometria, història de columna i espacial, forma física.

Les dades pertanyen a una **sessió**: `exercise` (protocol estandarditzat, p. ex. estirada al pit dret, amb càrrega declarada) o `spine_check` (mesura passiva curta). Només són comparables les dades amb el mateix `protocol_id`, i Terra enfront d'espai es llegeix a través de `gravity_environment`.

## Convencions
- Camps i descripcions en anglès, `snake_case`.
- **Unitats canòniques fixades per canal** (g, deg/s, mV, ohm, mm, cm, kg, Hz): no es converteix res en rebre.
- **Els payloads MQTT són chunks**: un stream, mostres consecutives, un array per canal, `seq` per detectar pèrdues. Cada stream té la seva pròpia freqüència.
- **Temps**: `t0_device_ms` (monòton) + `boot_id` al dispositiu; el gateway afegeix `received_at` (UTC) i publica àncores `clock-sync`. Tota la resta és RFC 3339 amb desfasament.
- **Qualitat**: score 0..1 més `flags` enumerades per canal i chunk, i indica qui l'ha avaluada.
- `source` és `device`, `simulator` o `replay`: una demo que barregi dades reals i simulades ho ha de dir.
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

## Canviar un contracte
Edita l'schema, afegeix o actualitza el seu exemple, regenera, executa els tests i avisa els amos de les altres capes. Les ampliacions compatibles pugen la versió menor; qualsevol altra cosa, la major.

## Obert
- **Aguado:** validar el catàleg de features (ids, unitats, `risk_direction`, rangs i les seves fonts); el conjunt reduït d'ítems de l'ODI; el protocol exacte de l'exercici (repeticions, càrrega, procediment de MVC) i la col·locació d'elèctrodes i IMU; el model del cordó (no és lineal i triga a recuperar-se, així que la longitud surt d'una corba mesurada).
- **Polfer:** el mòdul EMG dona el senyal cru o una envolvent? (les features espectrals necessiten cru). Fer servir pins ADC1 de l'ESP32 (l'ADC2 no funciona amb Bluetooth/WiFi). El format wearable→gateway és lliure; el contracte comença a MQTT.
- **Freqüència de la IMU:** 100 Hz per defecte; 10 Hz es queda curta per a suavitat i velocitat de pic d'una repetició de ~2 s.
- Encara sense cobrir: eines MCP i API de només lectura de l'app, informe a Houston (FHIR/DTN), tipus TypeScript per a la UI (es poden generar dels mateixos schemas).
- Revisió: Andreea. Validació semàntica: Aguado.

## Limitacions conegudes
- En microgravetat l'acceleròmetre no veu la gravetat: les features de la IMU que depenen de l'orientació no són comparables amb la Terra; el catàleg prefereix les basades en el giroscopi.
- La llibreria Python `jsonschema` aplica `pattern` amb `re.search`, i allà `$` admet un salt de línia final; per això `topics` fa servir `fullmatch`.
- Els models generats fan servir tipus `constr(...)` perquè els elements de llista amb restriccions continuïn sent cadenes planes; els analitzadors de tipus estàtics poden marcar-los.
