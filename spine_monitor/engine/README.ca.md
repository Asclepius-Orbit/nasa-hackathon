[English](README.md) · [Español](README.es.md) · **Català**

# spine-engine

Motor predictiu sense estat: rep el perfil de l'astronauta, una baseline, conjunts de features, autoinformes i context diari (`AssessRequest`) i retorna un `Assessment`. Les features que no es poden comparar amb la baseline es declaren com a absents a la completesa de l'avaluació, mai com a verd per defecte. Defineix les interfícies `BaselineStrategy`, `RiskModel` i `TriagePolicy`, un `AssessmentPipeline` que les uneix i stubs nuls (`stub_pipeline()`).

## Com arrencar-lo

Des del workspace `spine_monitor`:

```bash
uv run spine-engine                      # http://127.0.0.1:8001
sh ../app/mosquitto/setup.sh             # un cop: credencials del broker a app/.env
docker compose -f ../app/docker-compose.yml up --build engine
```

El contracte és `contracts/openapi.yaml`; cossos i errors són els JSON Schemas de `contracts/schemas/v1/engine/`. El motor només escolta a localhost.

| Endpoint | Cos d'entrada | Cos de sortida | Avui |
|---|---|---|---|
| `GET /health` | - | `{"status": "ok"}` | real |
| `POST /features` | `FeaturesRequest` (dades crues de sessió) | `FeatureSet` | famílies registrades: `exercise`, `emg`, `imu`, `emg_spectral` i `stretch` |
| `POST /baseline` | `BaselineRequest` (feature sets de les sessions de referència) | `Baseline` | real |
| `POST /assess` | `AssessRequest` | `Assessment` | comparació real amb la baseline i model de risc genèric (`risk_score`, `confidence`, `drivers`); triatge declaratiu provisional (política per defecte, veure Triatge declaratiu) |
| `POST /simulate` | `SimulateRequest` | `SimulateResponse` | aplica `overrides` (`set`, `add`, `scale`) a l'últim valor de cada feature, projecta tendències opcionalment i reavalua |

Quan s'indica `project_days`, el motor ordena les observacions de cada feature per `computed_at`, aplica els overrides al seu últim valor, ajusta una regressió lineal a l'historial resultant i projecta des d'aquest últim valor durant els dies sol·licitats abans de reavaluar. L'horitzó no pot superar l'interval temporal de cap feature amb almenys dues dates d'observació diferents; altrament es retorna un error. Les features amb menys de dues dates diferents conserven l'últim valor i s'enumeren a `projection.unprojected_features`. L'objecte opcional `projection` de la resposta també indica `project_days`. El resultat és hipotètic i no és una predicció clínica. Sense `project_days`, es conserva el comportament de simulació només amb overrides i s'omet `projection`.

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
| `FeatureExtractor` | `CompositeFeatureExtractor` | extreu `exercise` (#41), `emg` (#42), `imu` (#44), `emg_spectral` (#43) i `stretch` (#45) |
| `RiskModel` | `DeviationRiskModel` | suma el \|z\| de les desviacions sostingudes, ponderat per domini i per persistència (`n_points`), saturat a [0, 1] amb `1 - exp(-suma / escala)`; sense catàleg, qualsevol sentit compta com a pitjor i el signe queda al `deviation_z` de cada driver. El pipeline per defecte connecta el catàleg de features (`spine_contracts.catalog`) mitjançant el protocol petit `CatalogView` (`risk_direction`, `ranges`), i el model només el llegeix: les direccions i els rangs viuen al catàleg (provisionals, pendents de validació clínica), mai al codi. Regles de direcció, usant `Deviation.direction` (o el signe de z si falta): `lower_is_worse` compta només una disminució, `higher_is_worse` només un augment, `none` mai no compta, `either_is_worse` i `unspecified` usen \|z\| com abans. Una desviació en el sentit bo aporta 0 i no és driver. Rangs: si la feature té rangs, el nivell (normal, alert, critical) els límits `min`/`max` del qual contenen el valor del driver (un límit absent és sense cota, els límits són inclusius, el nivell més greu guanya en una vora compartida) multiplica la seva aportació per `RANGE_LEVEL_WEIGHTS` a `risk.py` (normal 0.5, alert 1.0, critical 2.0; ASSUMPTION: pesos provisionals, pendents d'Aguado). Un valor fora de tot rang, una feature sense rangs o un id que el catàleg no coneix manté el model z genèric. `drivers` porta la quota del score de cada feature, la més gran primer; `confidence` és la completesa. Pesos, persistència i escala: `SPINE_ENGINE_RISK_DOMAIN_WEIGHTS`, `SPINE_ENGINE_RISK_PERSISTENCE_POINTS`, `SPINE_ENGINE_RISK_SATURATION`. Un mapa de pesos parcial es completa amb 1.0 als dominis que falten. `n_points` és la llargada de la sèrie, no del seu tram final desviat, així que amb una persistència igual o menor al mínim de punts del detector el factor val sempre 1 (com amb el valor per defecte) |

Els detectors només veuen una sèrie de z-scores: no saben quina mètrica és, així que no depenen del model teòric. `Deviation` porta el z-score, la gravetat de referència i si el canvi és sostingut i en quin sentit; el model de risc ho llegeix d'allà.

## Extracció de features

`POST /features` executa `CompositeFeatureExtractor` amb `default_registry()`, que registra `exercise`, `emg`, `imu`, `emg_spectral` i `stretch` en aquest ordre. Cada família declara els IDs del catàleg i retorna features calculades i omeses. `NullFeatureExtractor` continua disponible per als tests.

Abans d'executar les famílies, el motor prepara una sola vegada un `ExtractionContext`:

- **Chunks:** només s'usen els de la sessió i de streams declarats. S'ordenen per (stream, `boot_id`, `seq`); d'un duplicat guanya la primera còpia; cada `boot_id` és un segment independent (mai s'uneixen mostres de dues arrencades) i un reinici es reporta com a forat. Els chunks perduts (salts de `seq`) i els `dropped_samples` compten com a mostres absents.
- **Rellotge:** `device_ms` passa a UTC per `boot_id` amb una recta de mínims quadrats sobre els seus `clock_syncs` (amb una sola àncora, un desfasament fix). Una arrencada sense àncores no té UTC: les seves dades no alimenten cap feature (`no_data`). `window` és el rang UTC dels chunks mapats i s'omet si no hi ha rellotge.
- **Calibratge:** el `calibration_id` del chunk mana sobre el de la sessió; es busca a `calibrations` per `stream_id` (i tipus). Sense coincidència: `no_calibration`; `valid_until` anterior al final de la finestra: `calibration_expired`. Mai un 422.
- **Qualitat:** `score = cobertura x (1 - fracció saturada)`, `assessed_by = "engine"`, amb flags de forats (`packet_gap`), saturació (`saturated_adc`, `clipping`), calibratge caducat (`out_of_calibration`) i, si es configura `clock_uncertain_ms`, `clock_uncertain`. La saturació només es jutja on les dades ho permeten (bits de l'ADC, rangs de la IMU); no hi ha llindars clínics.

El compost omple `unit` des del catàleg de features i la qualitat sobre els streams que cada feature ha declarat usar. Un id fora del catàleg és un error de programació de la família i llança `UnknownFeatureIdError`. El que no es pot calcular va a `skipped` amb motiu: `stream_missing` (stream declarat sense chunks), `no_calibration`, `calibration_expired` i `no_data` (cobertura 0, sense UTC o valor no finit), entre d'altres; la petició mai es rebutja per incoherent. `features` i `skipped` surten ordenades, així que el resultat no depèn de l'ordre de registre.

### Features implementades i validació contra el simulador

Features implementades per família (els IDs s'expandeixen amb la col·locació dels streams; les unitats surten del catàleg de features):

| Família | Features |
|---|---|
| `exercise` | `exercise.rep_count`, `exercise.rep_duration_s_mean`, `exercise.rep_duration_cv_pct` |
| `emg` | `emg.{muscle}.{side}.rms_mv`, `rest_rms_mv`, `rms_pct_mvc`, `peak_pct_mvc` i `emg.{muscle}.symmetry_index_pct` |
| `emg_spectral` | `emg.{muscle}.{side}.median_freq_hz`, `median_freq_slope_hz_per_rep` |
| `imu` | `imu.{level}.{plane}.rom_deg`, `peak_angular_velocity_dps` (sagital, frontal i transversal) i `imu.{level}.accel_rms_g` |
| `stretch` | `stretch.{spine_region}.elongation_mm`, `elongation_pct`, `peak_elongation_mm`, `range_mm`, `recovery_time_s`, `rest_resistance_ohm` |

Totes es validen de cap a cap contra la veritat de referència del simulador (`expected_features`): les sessions generades per `spine_physiology` passen per `CompositeFeatureExtractor(default_registry())` i cada valor es compara amb la veritat sota una tolerància explícita, per a dues llavors i per a gravetat terrestre i microgravetat (`engine/tests/test_features_*_simulator.py`). La veritat és el model sense soroll de sensor, així que les toleràncies absorbeixen el soroll, el calibratge mesurat i la histèresi simulada. El simulador no publica veritat per a `stretch.{spine_region}.recovery_time_s`, així que aquesta només es comprova per plausibilitat.

Els casos de fallada també es construeixen a partir de la sortida del simulador, i cadascun ha d'acabar a `skipped` amb el motiu correcte, sense inventar res:

- sense calibratge: `no_calibration` (el cordó conserva `rest_resistance_ohm`, que no necessita cap; la IMU no conserva res);
- EMG enviat com a envolupant: `signal_not_raw` per a les features espectrals;
- un stream absent: `stream_missing` exactament per a les features d'aquell stream, la resta sense canvis;
- forats de seq (els `lost_chunks` de la veritat): les features es continuen calculant, marcades amb `packet_gap` i amb la cobertura com a puntuació de qualitat, no s'omiten;
- IMU a 10 Hz: les features es continuen calculant a prop de la veritat; la freqüència de mostratge mai les omet, així que no s'emet `insufficient_sample_rate`.

Una última prova tanca el cercle: sessions normals simulades passen per `POST /features`, `POST /baseline` construeix el baseline a partir d'aquests conjunts de features, i `POST /assess` d'una sessió normal més mostra risc zero i cap driver (un desplaçament sostingut de les mateixes sessions sí que es desvia, com a control).

### Repeticions i la família `exercise`

`exercise.rep_count`, `exercise.rep_duration_s_mean` i `exercise.rep_duration_cv_pct` (CV en %, desviació típica mostral n-1) necessiten almenys 2 repeticions vàlides. Amb menys, les tres surten a `skipped` amb `too_few_repetitions`.

`RepetitionService` resol les finestres actives i de repòs. El registre comparteix un servei entre les famílies d'exercici, EMG, IMU, espectral i cordó.

L'origen, en aquest ordre:

1. Els esdeveniments `rep_start`/`rep_end` de la sessió (que registra la comanda `mark_event`), ordenats per temps i aparellats per ordre. Els orfes, els duplicats i les finestres amb fi <= inici es descarten. `rep_index` compta al llarg de tota la sessió, no per sèrie; `set_start`/`set_end`/`rest_start` només separen sèries, així que el descans entre sèries mai no és un repòs entre repeticions (l'última repetició d'una sèrie no té finestra de repòs; el suport IMU aplica la mateixa regla a les pauses de més de 5 vegades l'habitual).
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
- **`imu.{nivell}.accel_rms_g`:** RMS de la magnitud de l'acceleració després de treure a cada eix la seva mitjana a la repetició (la component estàtica: gravetat i biaix de l'acceleròmetre).

Cada valor és la mitjana sobre les repeticions amb mostres, i `n` les compta; una repetició llegeix el segment (`boot_id`) amb més mostres a la seva finestra. Motius de `skipped`, en ordre: `stream_missing`, `no_calibration`/`calibration_expired`, després `no_data` (sense rellotge o sense mostres a cap repetició) o `too_few_repetitions` (cap repetició). La freqüència de mostreig no descarta cap feature d'aquesta família.

### Fatiga espectral de l'EMG i la família `emg_spectral`

L'anàlisi espectral fa servir el fragment continu més llarg de cada repetició, separat per pèrdues de paquets, salts de rellotge, reinicis i canvis de freqüència de mostreig. Cada fragment requereix almenys 64 mostres.

Per a cada stream EMG declarat amb múscul i costat, `emg.{muscle}.{side}.median_freq_hz` és la freqüència mediana del PSD de Welch de la fase activa de cada repetició, promitjada entre les repeticions vàlides, i `median_freq_slope_hz_per_rep` és el pendent d'un ajust lineal d'aquesta freqüència mediana contra l'índex de repetició: la tendència de fatiga, negativa quan l'espectre baixa de banda. `n` és el nombre de repeticions que han aportat.

Necessita l'EMG cru: l'espectre d'un senyal rectificat o d'envolupant no descriu el múscul. Si els chunks del stream són rectificats o d'envolupant, totes dues features van a `skipped` amb `signal_not_raw`. Els altres motius són `too_few_repetitions` (menys de 2 repeticions vàlides, així que no hi ha pendent), `stream_missing` (stream declarat sense chunks) i `no_data` (repeticions sense prou mostres). Mai no llança. Es registra a `default_registry()` abans de `stretch`.

### La família del cordó (`stretch`)

Es calcula des de comptes ADC, així que no necessita senyal crua més enllà del chunk. Per a cada stream d'estirament declarat amb `spine_region`:

- **Resistència:** amb el `circuit` del chunk (`adc_bits`, `vref_v`, `series_resistor_ohm`) s'inverteix el divisor `Vout = vref * R / (R + Rs)` fins a una resistència. Sense `circuit`, s'usa la `resistance_ohm` derivada del chunk; si no n'hi ha cap, la feature va a `skipped`.
- **Longitud:** la resistència passa a longitud per la corba `piecewise_linear` de `calibration.stretch_calibration` (resistència a longitud, no lineal). `elongation_mm` i `elongation_pct` són respecte al seu `rest_length_mm`.
- **Features:** `stretch.{spine_region}.elongation_mm`, `elongation_pct`, `peak_elongation_mm`, `range_mm`, `recovery_time_s` (per repetició, reutilitzant `RepetitionService`) i `rest_resistance_ohm` (la resistència en repòs).
- **Espera de recuperació:** el cordó recupera en 1-2 min. L'espera és `stretch_calibration.recovery_wait_s` si ve; si no, `SPINE_ENGINE_STRETCH_RECOVERY_WAIT_S` (per defecte 120 s), PROVISIONAL fins a #46 (el valor real el fixa el protocol).
- **Calibratge:** com a la resta, el `calibration_id` del chunk mana sobre el de la sessió. Sense calibratge: `skipped` amb `no_calibration`; caducat: `calibration_expired`.

### Símptomes

`/assess` calcula features de símptomes de set dies a partir dels autoinformes i el context diari de l'astronauta fins a `as_of`; els valors absents consten com a dades mancants. La baseline calcula els mateixos agregats per a cada sessió de referència fins al final de la seva finestra (o la data de càlcul si no existeix), amb la gravetat de la sessió. El backend aporta els informes d'aquestes finestres. Els agregats són provisionals i no són llindars clínics.

## Configuració

Variables d'entorn (o un `.env`), totes opcionals:

| Variable | Per defecte | Significat |
|---|---|---|
| `SPINE_ENGINE_HOST` / `SPINE_ENGINE_PORT` | `127.0.0.1` / `8001` | on escolta (la imatge Docker posa el host a `0.0.0.0`) |
| `SPINE_ENGINE_SCALE_FLOOR` | `0.05` | sòl de l'escala del z-score, com a fracció de la mediana, quan MAD i desviació són ~0 |
| `SPINE_ENGINE_CUSUM_K` / `_H` / `_CLIP` / `_MIN_POINTS` | `0.5` / `4.0` / `3.0` / `3` | tolerància, llindar, topall per punt i històric mínim de CUSUM |
| `SPINE_ENGINE_KALMAN_PROCESS_VAR` / `_MEASUREMENT_VAR` / `_THRESHOLD` / `_PERSIST` / `_MIN_POINTS` | `0.05` / `1.0` / `1.0` / `3` / `3` | filtre de Kalman de nivell local |
| `SPINE_ENGINE_STRETCH_RECOVERY_WAIT_S` | `120` | espera de recuperació del cordó en segons quan manca `stretch_calibration.recovery_wait_s`; PROVISIONAL fins a #46 |
| `SPINE_ENGINE_STRETCH_RECOVERY_FRACTION` | `0.1` | fracció provisional del pic de repetició usada com a llindar de recuperació; configurable fins a #46 |

## Tests

```bash
uv run pytest engine
```
# Triatge declaratiu

El motor inclou una política per defecte **provisional**, `src/spine_engine/triage-policy.json` (empaquetada amb el motor, carregada per `PolicyConfig.default()`). Aguado encara no ha lliurat les regles clíniques: cada regla i tall de sota porta un `source` que comença per "provisional, pendiente de Aguado" i res d'això és una validació clínica. `SPINE_ENGINE_TRIAGE_POLICY_FILE` pot apuntar opcionalment a una política JSON que substitueix completament la per defecte; `stub_pipeline` manté els models nuls (nivell sempre verd).

Comportament: un risc igual o superior a `red_cutoff` dona nivell de model vermell, igual o superior a `amber_cutoff` ambre, altrament verd (els talls són inclusius). Les dades incompletes eleven el nivell global a ambre, i les banderes no reconegudes exigeixen escalat immediat i eleven el nivell a vermell (aquestes dues regles integrades són `RULE_UNKNOWN_RED_FLAG` i `RULE_INCOMPLETE_DATA`). S'ignoren els informes posteriors a `as_of`. Una feature absent també eleva a ambre el domini identificable.

El JSON admet `amber_cutoff` i `red_cutoff` junts (inclusius, entre 0 i 1), `unknown_flag_urgency`, un `source` opcional i una llista `rules`. Cada regla conté `rule_id`, `datum` (`risk`, `deviation`, `self_report` o `red_flag`), `key`, `operator`, `threshold`, `level`, `domain`, `recommendations`, `urgency` opcional i `source` opcional; per a banderes la urgència és obligatòria. Les claus dels informes són `pain_nrs`, `pain_nrs_worst_24h` i `stiffness_nrs`. Les regles d'exemple fictícies viuen només als tests.

Talls per defecte: `amber_cutoff` 0.3, `red_cutoff` 0.8, `unknown_flag_urgency` `immediate`. Són marcadors de posició: els talls reals de risc global per als nivells de triatge s'han de decidir amb criteri clínic i, fins aleshores, qualsevol configuració de talls és provisional.

| Regla | Datum | Nivell | Urgència | Recomanacions | Base provisional |
|---|---|---|---|---|---|
| `RULE_RED_FLAG_BLADDER_OR_BOWEL` | red_flag `bladder_or_bowel_dysfunction` | vermell | immediate | `REC_CONTACT_FLIGHT_SURGEON` | la disfunció d'esfínters és un signe d'alarma clàssic de la cauda equina; qualsevol informe escala de seguida |
| `RULE_RED_FLAG_SADDLE_NUMBNESS` | red_flag `saddle_numbness` | vermell | immediate | `REC_CONTACT_FLIGHT_SURGEON` | l'anestèsia en sella és un signe d'alarma clàssic de la cauda equina; qualsevol informe escala de seguida |
| `RULE_RED_FLAG_LEG_WEAKNESS` | red_flag `leg_weakness` | vermell | immediate | `REC_CONTACT_FLIGHT_SURGEON` | el dèficit motor suggereix compromís significatiu de l'arrel nerviosa o de la medul·la; es tracta com els signes de la cauda equina |
| `RULE_RED_FLAG_FEVER_WITH_BACK_PAIN` | red_flag `fever_with_back_pain` | vermell | immediate | `REC_CONTACT_FLIGHT_SURGEON` | la febre amb dolor d'esquena pot indicar infecció espinal; es tracta com una emergència pendent de revisió clínica |
| `RULE_RED_FLAG_TRAUMA` | red_flag `trauma_event` | vermell | priority | `REC_CONTACT_FLIGHT_SURGEON` | un esdeveniment traumàtic amb símptomes d'esquena pot implicar fractura o lesió; revisió prioritària, nivell d'urgència que fixarà Aguado |
| `RULE_RED_FLAG_RADIATING_LEG_PAIN` | red_flag `radiating_leg_pain` | ambre | priority | `REC_CONTACT_FLIGHT_SURGEON` | el dolor radicular suggereix irritació de l'arrel nerviosa; marcat per a revisió mèdica prioritària sense forçar vermell |
| `RULE_RED_FLAG_NUMBNESS_OR_TINGLING` | red_flag `numbness_or_tingling` | ambre | priority | `REC_CONTACT_FLIGHT_SURGEON` | els símptomes sensitius suggereixen afectació nerviosa; marcat per a revisió mèdica prioritària sense forçar vermell |
| `RULE_RED_FLAG_SEVERE_NIGHT_PAIN` | red_flag `severe_night_pain` | ambre | priority | `REC_CONTACT_FLIGHT_SURGEON` | el dolor nocturn no mecànic és una bandera vermella convencional de patologia greu; marcat per a revisió prioritària |
| `RULE_SELF_REPORT_PAIN_SEVERE` | self_report `pain_nrs` >= 7 | ambre | - | `REC_CONTACT_FLIGHT_SURGEON` | un NRS de dolor de 7 o més en l'escala 0-10 és la convenció habitual per a dolor intens; tall pendent de confirmar |

Tots els codis de recomanació reutilitzen ids que la UI ja renderitza. Què ha de confirmar Aguado: els talls de risc, el nivell i la urgència de cada bandera vermella (en particular `trauma_event` i les tres banderes ambre), el llindar de dolor, si calen més regles (basades en desviació o en risc) i més codis de recomanació, i el redactat de cada recomanació.
