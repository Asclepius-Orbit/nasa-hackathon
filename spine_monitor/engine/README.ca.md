[English](README.md) · [Español](README.es.md) · **Català**

# spine-engine

Motor predictiu sense estat: rep el perfil de l'astronauta, una baseline, conjunts de features, autoinformes i context diari (`AssessRequest`) i retorna un `Assessment`. Les features que no es poden comparar amb la baseline es declaren com a absents a la completesa de l'avaluació, mai com a verd per defecte. Defineix les interfícies `BaselineStrategy`, `RiskModel` i `TriagePolicy`, un `AssessmentPipeline` que les uneix i stubs nuls (`stub_pipeline()`).

## Com arrencar-lo

Des del workspace `spine_monitor`:

```bash
uv run spine-engine                      # http://127.0.0.1:8001
docker compose -f ../app/docker-compose.yml up --build engine
```

El contracte és `contracts/openapi.yaml`; cossos i errors són els JSON Schemas de `contracts/schemas/v1/engine/`. El motor només escolta a localhost.

| Endpoint | Cos d'entrada | Cos de sortida | Avui |
|---|---|---|---|
| `GET /health` | - | `{"status": "ok"}` | real |
| `POST /features` | `FeaturesRequest` (dades crues de la sessió) | `FeatureSet` | `features` de les famílies registrades: `exercise` des de #41, `emg` (amplitud) des de #42 i `imu` des de #44, més `stretch` (cordó) des de #45; l'altra família de cordó arriba a #43 |
| `POST /baseline` | `BaselineRequest` (feature sets de les sessions de referència) | `Baseline` | real |
| `POST /assess` | `AssessRequest` | `Assessment` | comparació real amb la baseline i model de risc genèric (`risk_score`, `confidence`, `drivers`); triatge nul (nivell sempre verd) |
| `POST /simulate` | `SimulateRequest` | `SimulateResponse` | aplica `overrides` (`set`, `add`, `scale`) a l'últim valor de cada feature i reavalua; `project_days` encara no s'aplica |

Els errors són problem details (`application/problem+json`, `engine/error.schema.json`): `422` amb `invalid_request` (amb un JSON Pointer per problema), `unsupported_schema_version` o `insufficient_data`, i `500` amb `internal`. Els cossos es validen contra el JSON Schema a més de Pydantic, així que s'apliquen les regles condicionals.

```bash
curl -s -X POST http://127.0.0.1:8001/assess \
  -H 'content-type: application/json' \
  --data-binary @contracts/examples/valid/engine/assess-request.json
```

### Per fer-ne un mock

Els exemples de `contracts/examples/valid/engine/` són peticions vàlides de cada endpoint, i `contracts/examples/valid/domain/` té respostes vàlides (`assessment.amber.json`, `baseline.json`, `feature-set.*.json`). Un mock només ha de tornar-les per la ruta que toqui i respondre els errors amb `engine/error.json`.

## Baseline personal i desviacions

`POST /baseline` construeix una entrada per feature + protocol + entorn de gravetat (mitjana, desviació típica, mediana i MAD) a partir de les sessions de referència, normalment pre-vol en 1 g. Una feature vista en una sola sessió també té entrada (`n` diu quant fiar-se'n). Qui crida desa la baseline i l'envia a cada `/assess`.

`/assess` compara l'últim valor de cada feature amb l'entrada de la seva mateixa gravetat o, si no n'hi ha, amb la de la Terra. Tot s'injecta, així que canviar una peça és escriure una altra classe, no editar les existents:

| Interfície | Per defecte | Què fa |
|---|---|---|
| `DeviationScorer` | `RobustZScorer` | `(x - mediana) / (1.4826 * MAD)`; cau a la desviació típica i després a un sòl proporcional a la mediana |
| `ChangeDetector` | `CompositeDetector` de `CusumDetector` + `KalmanDetector` | canvi sostingut sobre l'històric (mateix protocol i gravetat); un pic aïllat mai compta |
| `VotePolicy` | `AllAgreeVote` | els detectors han de coincidir (AND), en el mateix sentit |
| `BaselineBuilder` | `StatisticalBaselineBuilder` | construeix la baseline |
| `FeatureExtractor` | `CompositeFeatureExtractor` | dades crues a features: executa les famílies registrades (`exercise`, #41; amplitud `emg`, #42; `imu`, #44; `stretch`, #45; l'altra de cordó, #43) |
| `RiskModel` | `DeviationRiskModel` | suma el \|z\| de les desviacions sostingudes, ponderat per domini i per persistència (`n_points`), saturat a [0, 1] amb `1 - exp(-suma / escala)`; sense rangs clínics, qualsevol sentit compta com a pitjor i el signe queda al `deviation_z` de cada driver. `drivers` porta la quota del score de cada feature, la més gran primer; `confidence` és la completesa. Pesos, persistència i escala: `SPINE_ENGINE_RISK_DOMAIN_WEIGHTS`, `SPINE_ENGINE_RISK_PERSISTENCE_POINTS`, `SPINE_ENGINE_RISK_SATURATION`. Un mapa de pesos parcial es completa amb 1.0 als dominis que falten. `n_points` és la llargada de la sèrie, no del seu tram final desviat, així que amb una persistència igual o menor al mínim de punts del detector el factor val sempre 1 (com amb el valor per defecte) |

Els detectors només veuen una sèrie de z-scores: no saben quina mètrica és, així que no depenen del model teòric. `Deviation` porta el z-score, la gravetat de referència i si el canvi és sostingut i en quin sentit; el model de risc ho llegeix d'allà.

## Extracció de features

`POST /features` executa `CompositeFeatureExtractor` sobre un `ExtractorRegistry` de famílies de features (`FeatureFamilyExtractor`: un nom `family`, els `feature_ids` del catàleg que declara i `extract(context) -> FamilyResult` amb features `computed` i `skipped`). `default_registry()` registra les famílies `exercise` (#41), `emg` d'amplitud (#42) i `imu` (#44), així que avui ja es calculen les mètriques de repeticions, d'amplitud de l'EMG i de la IMU, i des de #45 la família `stretch` del cordó; l'altra família de cordó arriba a #43. `NullFeatureExtractor` es manté a `stubs.py` per als tests.

Abans d'executar les famílies, el motor prepara una sola vegada un `ExtractionContext`:

- **Chunks:** només s'usen els de la sessió i de streams declarats. S'ordenen per (stream, `boot_id`, `seq`); d'un duplicat guanya la primera còpia; cada `boot_id` és un segment independent (mai s'uneixen mostres de dues arrencades) i un reinici es reporta com a forat. Els chunks perduts (salts de `seq`) i els `dropped_samples` compten com a mostres absents.
- **Rellotge:** `device_ms` passa a UTC per `boot_id` amb una recta de mínims quadrats sobre els seus `clock_syncs` (amb una sola àncora, un desfasament fix). Una arrencada sense àncores no té UTC: les seves dades no alimenten cap feature (`no_data`). `window` és el rang UTC dels chunks mapats i s'omet si no hi ha rellotge.
- **Calibratge:** el `calibration_id` del chunk mana sobre el de la sessió; es busca a `calibrations` per `stream_id` (i tipus). Sense coincidència: `no_calibration`; `valid_until` anterior al final de la finestra: `calibration_expired`. Mai un 422.
- **Qualitat:** `score = cobertura x (1 - fracció saturada)`, `assessed_by = "engine"`, amb flags de forats (`packet_gap`), saturació (`saturated_adc`, `clipping`), calibratge caducat (`out_of_calibration`) i, si es configura `clock_uncertain_ms`, `clock_uncertain`. La saturació només es jutja on les dades ho permeten (bits de l'ADC, rangs de la IMU); no hi ha llindars clínics.

El compost omple `unit` des del catàleg de features i la qualitat sobre els streams que cada feature ha declarat usar. Un id fora del catàleg és un error de programació de la família i llança `UnknownFeatureIdError`. El que no es pot calcular va a `skipped` amb motiu: `stream_missing` (stream declarat sense chunks), `no_calibration`, `calibration_expired` i `no_data` (cobertura 0, sense UTC o valor no finit), entre d'altres; la petició mai es rebutja per incoherent. `features` i `skipped` surten ordenades, així que el resultat no depèn de l'ordre de registre.

### Repeticions i la família `exercise`

`exercise.rep_count`, `exercise.rep_duration_s_mean` i `exercise.rep_duration_cv_pct` (CV en %, desviació típica mostral n-1) necessiten almenys 2 repeticions vàlides. Amb menys, les tres surten a `skipped` amb `too_few_repetitions`.

`RepetitionService` resol les finestres de repetició (`RepWindow`): inici, fi, la fase activa (rep_start→rep_end) i el repòs (rep_end→següent rep_start). L'última repetició només té repòs si després hi ha un esdeveniment `rest_end`. `default_registry()` crea un sol servei i se'l passa a les famílies, perquè les altres famílies (la d'EMG des de #42, la d'IMU des de #44 i la `stretch` des de #45) el reutilitzin.

L'origen, en aquest ordre:

1. Els esdeveniments `rep_start`/`rep_end` de la sessió (que registra la comanda `mark_event`), ordenats per temps i aparellats per ordre. Els orfes, els duplicats i les finestres amb fi <= inici es descarten.
2. Si no en queda cap de vàlida, recurs de reserva per IMU que usa només el giroscopi (en microgravetat l'acceleròmetre no veu la gravetat): lòbuls de velocitat angular de signe contrari a l'eix dominant, per `boot_id`, amb rellotge UTC. Sense senyal no retorna res.
3. Si tampoc hi ha res, no hi ha repeticions.

Qualitat: les features per esdeveniments avaluen la qualitat comuna sobre tots els streams declarats; les de recurs de reserva IMU, sobre els streams IMU. Sense chunks amb àncora de rellotge, surten `no_data`.

### Amplitud de l'EMG i la família `emg`

Per a cada stream EMG declarat amb múscul i costat, `emg.{muscle}.{side}.rms_mv` és el RMS de les mostres de la fase activa de totes les repeticions i `rest_rms_mv` el RMS dels seus repòs. `rms_pct_mvc` divideix `rms_mv` entre `mvc_rms_mv` de la calibració `emg_mvc` del stream; `peak_pct_mvc` és el RMS en finestra més alt (finestra = `window_ms` de la calibració, sense creuar d'una repetició ni d'un `boot_id` a un altre) sobre la mateixa MVC. `emg.{muscle}.symmetry_index_pct` és 100·|L-R| / ((L+R)/2) sobre els dos `rms_pct_mvc`. L'amplitud no necessita senyal crua: el càlcul és el mateix; el valor coincideix per a crua i rectificada, no per a l'envolvent. `n` és el nombre de repeticions que han aportat mostres. La MVC només s'usa si és del múscul i el costat del stream i es va mesurar en un tipus de senyal compatible amb tots els seus chunks (crua i rectificada entre si, envolvent només amb envolvent); si no, les features de %MVC i la simetria surten a `skipped` amb `no_calibration`, i `rms_mv` i `rest_rms_mv` es continuen calculant.

El que no es pot calcular va a `skipped`: `no_calibration` / `calibration_expired` per al que necessita la MVC (la simetria necessita tots dos costats; si en falta un, guanya sobre una caducada), `too_few_repetitions` sense repeticions, `no_data` sense mostres a les finestres (o amb mitjana nul·la a la simetria, o un repòs sense finestra de repòs) i `stream_missing` per a un stream declarat sense chunks o un múscul amb un sol costat declarat.

### La família `imu`

Basada en el giroscopi, així que no depèn de la gravetat i és comparable entre la Terra i la microgravetat. Per a cada stream IMU declarat, amb el `spine_level` del seu `placement` a la sessió (mai fixat al codi), i per a cada finestra de repetició (fase activa, `start <= t < end`):

- **Plans:** s'emeten tots tres. `calibration.imu.mounting` porta cada eix del sensor a un eix corporal: sagital = rotació sobre l'eix esquerra-dreta, frontal = sobre l'anterior, transversal = sobre el cranial (el mateix marc que el simulador). Un muntatge sense eix del sensor per a un pla deixa aquest pla a `no_calibration`. El `gyro_bias_dps` del calibratge es resta a cada eix del sensor.
- **`imu.{nivell}.{pla}.rom_deg`:** recorregut (màx - mín) de l'angle integrat (cada mostra amb el període del seu chunk), des de 0 abans de la primera mostra. **`peak_angular_velocity_dps`:** el `|ω|` més gran.
- **`imu.{nivell}.{pla}.smoothness_sparc`:** SPARC de `|ω|` amb la FFT de numpy i els valors per defecte publicats (farciment de zeros 2^4, tall a 10 Hz, llindar d'amplitud del 5 %; Balasubramanian et al. 2015). Només si tots els chunks del stream van a 50 Hz o més (el requisit del catàleg); si no, `skipped` amb `insufficient_sample_rate`. Sense moviment SPARC no està definit i acaba a `no_data`.
- **`imu.{nivell}.accel_rms_g`:** RMS de la magnitud de l'acceleració després de treure a cada eix la seva mitjana a la repetició (la component estàtica: gravetat i biaix de l'acceleròmetre).

Cada valor és la mitjana sobre les repeticions amb mostres i valor finit, i `n` les compta (una repetició sense moviment no té SPARC i no compta per a ell; si cap no en té, `no_data`); una repetició llegeix el segment (`boot_id`) amb més mostres a la seva finestra. Motius de `skipped`, en ordre: `stream_missing`, `no_calibration`/`calibration_expired`, després `no_data` (sense rellotge o sense mostres a cap repetició) o `too_few_repetitions` (cap repetició); l'`insufficient_sample_rate` de SPARC es decideix abans que aquests dos últims.

### La família del cordó (`stretch`)

Es calcula des de comptes ADC, així que no necessita senyal crua més enllà del chunk. Per a cada stream d'estirament declarat amb `spine_region`:

- **Resistència:** amb el `circuit` del chunk (`adc_bits`, `vref_v`, `series_resistor_ohm`) s'inverteix el divisor `Vout = vref * R / (R + Rs)` fins a una resistència. Sense `circuit`, s'usa la `resistance_ohm` derivada del chunk; si no n'hi ha cap, la feature va a `skipped`.
- **Longitud:** la resistència passa a longitud per la corba `piecewise_linear` de `calibration.stretch_calibration` (resistència a longitud, no lineal). `elongation_mm` i `elongation_pct` són respecte al seu `rest_length_mm`.
- **Features:** `stretch.{spine_region}.elongation_mm`, `elongation_pct`, `peak_elongation_mm`, `range_mm`, `recovery_time_s` (per repetició, reutilitzant `RepetitionService`) i `rest_resistance_ohm` (la resistència en repòs).
- **Espera de recuperació:** el cordó recupera en 1-2 min. L'espera és `stretch_calibration.recovery_wait_s` si ve; si no, `SPINE_ENGINE_STRETCH_RECOVERY_WAIT_S` (per defecte 120 s), PROVISIONAL fins a #46 (el valor real el fixa el protocol).
- **Calibratge:** com a la resta, el `calibration_id` del chunk mana sobre el de la sessió. Sense calibratge: `skipped` amb `no_calibration`; caducat: `calibration_expired`.

## Configuració

Variables d'entorn (o un `.env`), totes opcionals:

| Variable | Per defecte | Significat |
|---|---|---|
| `SPINE_ENGINE_HOST` / `SPINE_ENGINE_PORT` | `127.0.0.1` / `8001` | on escolta (la imatge Docker posa el host a `0.0.0.0`) |
| `SPINE_ENGINE_SCALE_FLOOR` | `0.05` | sòl de l'escala del z-score, com a fracció de la mediana, quan MAD i desviació són ~0 |
| `SPINE_ENGINE_CUSUM_K` / `_H` / `_CLIP` / `_MIN_POINTS` | `0.5` / `4.0` / `3.0` / `3` | tolerància, llindar, topall per punt i històric mínim de CUSUM |
| `SPINE_ENGINE_KALMAN_PROCESS_VAR` / `_MEASUREMENT_VAR` / `_THRESHOLD` / `_PERSIST` / `_MIN_POINTS` | `0.05` / `1.0` / `1.0` / `3` / `3` | filtre de Kalman de nivell local |
| `SPINE_ENGINE_STRETCH_RECOVERY_WAIT_S` | `120` | espera de recuperació del cordó en segons quan manca `stretch_calibration.recovery_wait_s`; PROVISIONAL fins a #46 |
| `SPINE_ENGINE_STRETCH_RECOVERY_FRACTION` | `0.1` | fracci? provisional del pic de repetici? usada com a llindar de recuperaci?; configurable fins a #46 |

## Tests

```bash
uv run pytest engine
```
