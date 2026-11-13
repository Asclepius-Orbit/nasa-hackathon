[English](README.md) · **Español** · [Català](README.ca.md)

# spine-engine

Motor predictivo sin estado: recibe el perfil del astronauta, una baseline, conjuntos de features, autoinformes y contexto diario (`AssessRequest`) y devuelve un `Assessment`. Las features que no se pueden comparar con la baseline se declaran como faltantes en la completitud de la evaluación, nunca como verde por defecto. Define las interfaces `BaselineStrategy`, `RiskModel` y `TriagePolicy`, un `AssessmentPipeline` que las junta y stubs nulos (`stub_pipeline()`).

## Cómo arrancarlo

Desde el workspace `spine_monitor`:

```bash
uv run spine-engine                      # http://127.0.0.1:8001
sh ../app/mosquitto/setup.sh             # una vez: credenciales del broker en app/.env
docker compose -f ../app/docker-compose.yml up --build engine
```

El contrato es `contracts/openapi.yaml`; cuerpos y errores son los JSON Schemas de `contracts/schemas/v1/engine/`. El motor solo escucha en localhost.

| Endpoint | Cuerpo de entrada | Cuerpo de salida | Hoy |
|---|---|---|---|
| `GET /health` | - | `{"status": "ok"}` | real |
| `POST /features` | `FeaturesRequest` (datos crudos de sesión) | `FeatureSet` | familias registradas: `exercise`, `emg`, `imu`, `emg_spectral` y `stretch` |
| `POST /baseline` | `BaselineRequest` (feature sets de las sesiones de referencia) | `Baseline` | real |
| `POST /assess` | `AssessRequest` | `Assessment` | comparación real con la baseline y modelo de riesgo genérico (`risk_score`, `confidence`, `drivers`); triaje declarativo provisional (política por defecto, ver Triaje declarativo) |
| `POST /simulate` | `SimulateRequest` | `SimulateResponse` | aplica `overrides` (`set`, `add`, `scale`) al último valor de cada feature, proyecta tendencias opcionalmente y reevalúa |

Cuando se indica `project_days`, el motor ordena las observaciones de cada feature por `computed_at`, aplica los overrides a su último valor, ajusta una regresión lineal al historial resultante y proyecta desde ese último valor durante los días solicitados antes de reevaluar. El horizonte no puede superar el intervalo temporal de ninguna feature con al menos dos fechas de observación distintas; de lo contrario se devuelve un error. Las features con menos de dos fechas distintas conservan su último valor y se enumeran en `projection.unprojected_features`. El objeto opcional `projection` de la respuesta también indica `project_days`. El resultado es hipotético y no es una predicción clínica. Sin `project_days`, se conserva el comportamiento de simulación con solo overrides y se omite `projection`.

Los errores son problem details (`application/problem+json`, `engine/error.schema.json`): `422` con `invalid_request` (con un JSON Pointer por problema), `unsupported_schema_version` o `insufficient_data`, y `500` con `internal`. Los cuerpos se validan contra el JSON Schema además de Pydantic, así que se aplican las reglas condicionales.

```bash
curl -s -X POST http://127.0.0.1:8001/assess \
  -H 'content-type: application/json' \
  --data-binary @contracts/examples/valid/engine/assess-request.json
```

### Para mockearlo

Los ejemplos de `contracts/examples/valid/engine/` son peticiones válidas de cada endpoint, y `contracts/examples/valid/domain/` tiene respuestas válidas (`assessment.amber.json`, `baseline.json`, `feature-set.*.json`). Un mock solo tiene que devolverlas para la ruta que toque y responder los errores con `engine/error.json`.

## Baseline personal y desviaciones

`POST /baseline` construye una entrada por feature + protocolo + entorno de gravedad (media, desviación típica, mediana y MAD) a partir de las sesiones de referencia, normalmente pre-vuelo en 1 g. Una feature vista en una sola sesión también tiene entrada (`n` dice cuánto fiarse). Quien llama guarda la baseline y la manda en cada `/assess`.

`/assess` compara el último valor de cada feature con la entrada de su misma gravedad o, si no hay, con la de la Tierra. Todo se inyecta, así que cambiar una pieza es escribir otra clase, no editar las existentes:

| Interfaz | Por defecto | Qué hace |
|---|---|---|
| `DeviationScorer` | `RobustZScorer` | `(x - mediana) / (1.4826 * MAD)`; cae a la desviación típica y luego a un suelo proporcional a la mediana |
| `ChangeDetector` | `CompositeDetector` de `CusumDetector` + `KalmanDetector` | cambio sostenido sobre el histórico (mismo protocolo y gravedad); un pico aislado nunca cuenta |
| `VotePolicy` | `AllAgreeVote` | los detectores deben coincidir (AND), en el mismo sentido |
| `BaselineBuilder` | `StatisticalBaselineBuilder` | construye la baseline |
| `FeatureExtractor` | `CompositeFeatureExtractor` | extrae `exercise` (#41), `emg` (#42), `imu` (#44), `emg_spectral` (#43) y `stretch` (#45) |
| `RiskModel` | `DeviationRiskModel` | suma el \|z\| de las desviaciones sostenidas, ponderado por dominio y por persistencia (`n_points`), saturado a [0, 1] con `1 - exp(-suma / escala)`; sin catálogo, cualquier sentido cuenta como peor y el signo queda en el `deviation_z` de cada driver. El pipeline por defecto conecta el catálogo de features (`spine_contracts.catalog`) mediante el protocolo pequeño `CatalogView` (`risk_direction`, `ranges`), y el modelo solo lo lee: las direcciones y los rangos viven en el catálogo (provisionales, pendientes de validación clínica), nunca en el código. Reglas de dirección, usando `Deviation.direction` (o el signo de z si falta): `lower_is_worse` cuenta solo una disminución, `higher_is_worse` solo un aumento, `none` nunca cuenta, `either_is_worse` y `unspecified` usan \|z\| como antes. Una desviación en el sentido bueno aporta 0 y no es driver. Rangos: si la feature tiene rangos, el nivel (normal, alert, critical) cuyos límites `min`/`max` contienen el valor del driver (un límite ausente es sin cota, los límites son inclusivos, el nivel más grave gana en un borde compartido) multiplica su aportación por `RANGE_LEVEL_WEIGHTS` en `risk.py` (normal 0.5, alert 1.0, critical 2.0; ASSUMPTION: pesos provisionales, pendientes de Aguado). Un valor fuera de todo rango, una feature sin rangos o un id que el catálogo no conoce mantiene el modelo z genérico. `drivers` lleva la cuota del score de cada feature, la mayor primero; `confidence` es la completitud. Pesos, persistencia y escala: `SPINE_ENGINE_RISK_DOMAIN_WEIGHTS`, `SPINE_ENGINE_RISK_PERSISTENCE_POINTS`, `SPINE_ENGINE_RISK_SATURATION`. Un mapa de pesos parcial se completa con 1.0 en los dominios que faltan. `n_points` es el largo de la serie, no de su tramo final desviado, así que con una persistencia igual o menor al mínimo de puntos del detector el factor vale siempre 1 (como con el valor por defecto) |

Los detectores solo ven una serie de z-scores: no saben qué métrica es, así que no dependen del modelo teórico. `Deviation` lleva el z-score, la gravedad de referencia y si el cambio es sostenido y en qué sentido; el modelo de riesgo lo lee de ahí.

## Extracción de features

`POST /features` ejecuta `CompositeFeatureExtractor` con `default_registry()`, que registra `exercise`, `emg`, `imu`, `emg_spectral` y `stretch` en ese orden. Cada familia declara sus IDs del catálogo y devuelve features calculadas y omitidas. `NullFeatureExtractor` permanece disponible para tests.

Antes de ejecutar las familias, el motor prepara una sola vez un `ExtractionContext`:

- **Chunks:** solo se usan los de la sesión y de streams declarados. Se ordenan por (stream, `boot_id`, `seq`); de un duplicado gana la primera copia; cada `boot_id` es un segmento independiente (nunca se unen muestras de dos arranques) y un reinicio se reporta como hueco. Los chunks perdidos (saltos de `seq`) y los `dropped_samples` cuentan como muestras ausentes.
- **Reloj:** `device_ms` pasa a UTC por `boot_id` con una recta de mínimos cuadrados sobre sus `clock_syncs` (con una sola ancla, un desfase fijo). Un arranque sin anclas no tiene UTC: sus datos no alimentan ninguna feature (`no_data`). `window` es el rango UTC de los chunks mapeados y se omite si no hay reloj.
- **Calibración:** el `calibration_id` del chunk manda sobre el de la sesión; se busca en `calibrations` por `stream_id` (y tipo). Sin coincidencia: `no_calibration`; `valid_until` anterior al final de la ventana: `calibration_expired`. Nunca un 422.
- **Calidad:** `score = cobertura x (1 - fracción saturada)`, `assessed_by = "engine"`, con flags de huecos (`packet_gap`), saturación (`saturated_adc`, `clipping`), calibración caducada (`out_of_calibration`) y, si se configura `clock_uncertain_ms`, `clock_uncertain`. La saturación solo se juzga donde los datos lo permiten (bits del ADC, rangos de la IMU); no hay umbrales clínicos.

El compuesto rellena `unit` desde el catálogo de features y la calidad sobre los streams que cada feature declaró usar. Un id fuera del catálogo es un error de programación de la familia y lanza `UnknownFeatureIdError`. Lo que no se puede calcular va a `skipped` con motivo: `stream_missing` (stream declarado sin chunks), `no_calibration`, `calibration_expired` y `no_data` (cobertura 0, sin UTC o valor no finito), entre otros; la petición nunca se rechaza por incoherente. `features` y `skipped` salen ordenadas, así que el resultado no depende del orden de registro.

### Repeticiones y la familia `exercise`

`exercise.rep_count`, `exercise.rep_duration_s_mean` y `exercise.rep_duration_cv_pct` (CV en %, desviación típica muestral n-1) necesitan al menos 2 repeticiones válidas. Con menos, las tres salen en `skipped` con `too_few_repetitions`.

`RepetitionService` resuelve las ventanas activas y de reposo. El registro comparte un servicio entre las familias de ejercicio, EMG, IMU, espectral y cordón.

El origen, en este orden:

1. Los eventos `rep_start`/`rep_end` de la sesión (que registra el comando `mark_event`), ordenados por tiempo y emparejados por orden. Los huérfanos, los duplicados y las ventanas con fin <= inicio se descartan. `rep_index` cuenta a lo largo de toda la sesión, no por serie; `set_start`/`set_end`/`rest_start` solo separan series, así que el descanso entre series nunca es un reposo entre repeticiones (la última repetición de una serie no tiene ventana de reposo; el respaldo IMU aplica la misma regla a las pausas de más de 5 veces la habitual).
2. Si no queda ninguna ventana válida, respaldo por IMU que usa solo el giróscopo (en microgravedad el acelerómetro no ve la gravedad): lóbulos de velocidad angular de signo contrario en el eje dominante, por `boot_id`, con reloj UTC. Sin señal no devuelve nada.
3. Si tampoco hay nada, no hay repeticiones.

Calidad: las features por eventos evalúan la calidad común sobre todos los streams declarados; las de respaldo IMU, sobre los streams IMU. Sin chunks con ancla de reloj, salen `no_data`.

### Amplitud del EMG y la familia `emg`

Para cada stream EMG declarado con músculo y lado, `emg.{muscle}.{side}.rms_mv` es el RMS de las muestras de la fase activa de todas las repeticiones y `rest_rms_mv` el RMS de sus reposos. `rms_pct_mvc` divide `rms_mv` entre `mvc_rms_mv` de la calibración `emg_mvc` del stream; `peak_pct_mvc` es el RMS en ventana más alto (ventana = `window_ms` de la calibración, sin cruzar de una repetición ni de un `boot_id` a otro) sobre la misma MVC. `emg.{muscle}.symmetry_index_pct` es 100·|L-R| / ((L+R)/2) sobre los dos `rms_pct_mvc`. La amplitud no necesita señal cruda: el cálculo es el mismo; el valor coincide para cruda y rectificada, no para la envolvente. `n` es el número de repeticiones que aportaron muestras. La MVC solo se usa si es del músculo y el lado del stream y se midió en un tipo de señal compatible con todos sus chunks (cruda y rectificada entre sí, envolvente solo con envolvente); si no, las features de %MVC y la simetría salen en `skipped` con `no_calibration`, y `rms_mv` y `rest_rms_mv` se siguen calculando.

Lo que no se puede calcular va a `skipped`: `no_calibration` / `calibration_expired` para lo que necesita la MVC (la simetría necesita ambos lados; si falta una, gana sobre una caducada), `too_few_repetitions` sin repeticiones, `no_data` sin muestras en las ventanas (o con media nula en la simetría, o un reposo sin ventana de reposo) y `stream_missing` para un stream declarado sin chunks o un músculo con un solo lado declarado.

### La familia `imu`

Basada en el giróscopo, así que no depende de la gravedad y es comparable entre Tierra y microgravedad. Para cada stream IMU declarado, con el `spine_level` de su `placement` en la sesión (nunca fijado en el código), y para cada ventana de repetición (fase activa, `start <= t < end`):

- **Planos:** se emiten los tres. `calibration.imu.mounting` lleva cada eje del sensor a un eje corporal: sagital = rotación sobre el eje izquierda-derecha, frontal = sobre el anterior, transversal = sobre el craneal (el mismo marco que el simulador). Un montaje sin eje del sensor para un plano deja ese plano en `no_calibration`. El `gyro_bias_dps` de la calibración se resta a cada eje del sensor.
- **`imu.{nivel}.{plano}.rom_deg`:** recorrido (máx - mín) del ángulo integrado (cada muestra con el periodo de su chunk), desde 0 antes de la primera muestra. **`peak_angular_velocity_dps`:** el mayor `|ω|`.
- **`imu.{nivel}.accel_rms_g`:** RMS de la magnitud de la aceleración tras quitar a cada eje su media en la repetición (la componente estática: gravedad y sesgo del acelerómetro).

Cada valor es la media sobre las repeticiones con muestras, y `n` las cuenta; una repetición lee el segmento (`boot_id`) con más muestras en su ventana. Motivos de `skipped`, en orden: `stream_missing`, `no_calibration`/`calibration_expired`, luego `no_data` (sin reloj o sin muestras en ninguna repetición) o `too_few_repetitions` (ninguna repetición). La frecuencia de muestreo no descarta ninguna feature de esta familia.

### Fatiga espectral del EMG y la familia `emg_spectral`

El análisis espectral usa el fragmento continuo más largo de cada repetición, separado por pérdidas de paquetes, saltos de reloj, reinicios y cambios de frecuencia de muestreo. Cada fragmento requiere al menos 64 muestras.

Para cada stream EMG declarado con músculo y lado, `emg.{muscle}.{side}.median_freq_hz` es la frecuencia mediana del PSD de Welch de la fase activa de cada repetición, promediada entre las repeticiones válidas, y `median_freq_slope_hz_per_rep` es la pendiente de un ajuste lineal de esa frecuencia mediana contra el índice de repetición: la tendencia de fatiga, negativa cuando el espectro baja de banda. `n` es el número de repeticiones que aportaron.

Necesita el EMG crudo: el espectro de una señal rectificada o de envolvente no describe el músculo. Si los chunks del stream son rectificados o envolvente, ambas features van a `skipped` con `signal_not_raw`. Los demás motivos son `too_few_repetitions` (menos de 2 repeticiones válidas, así que no hay pendiente), `stream_missing` (stream declarado sin chunks) y `no_data` (repeticiones sin muestras suficientes). Nunca lanza. Se registra en `default_registry()` antes de `stretch`.

### La familia del cordón (`stretch`)

Se calcula desde cuentas ADC, así que no necesita señal cruda más allá del chunk. Para cada stream de estiramiento declarado con `spine_region`:

- **Resistencia:** con el `circuit` del chunk (`adc_bits`, `vref_v`, `series_resistor_ohm`) se invierte el divisor `Vout = vref * R / (R + Rs)` hasta una resistencia. Sin `circuit`, se usa la `resistance_ohm` derivada del chunk; si no hay ninguno, la feature va a `skipped`.
- **Longitud:** la resistencia pasa a longitud por la curva `piecewise_linear` de `calibration.stretch_calibration` (resistencia a longitud, no lineal). `elongation_mm` y `elongation_pct` son respecto a su `rest_length_mm`.
- **Features:** `stretch.{spine_region}.elongation_mm`, `elongation_pct`, `peak_elongation_mm`, `range_mm`, `recovery_time_s` (por repetición, reutilizando `RepetitionService`) y `rest_resistance_ohm` (la resistencia en reposo).
- **Espera de recuperación:** el cordón recupera en 1-2 min. La espera es `stretch_calibration.recovery_wait_s` si viene; si no, `SPINE_ENGINE_STRETCH_RECOVERY_WAIT_S` (defecto 120 s), PROVISIONAL hasta #46 (el valor real lo fija el protocolo).
- **Calibración:** como en el resto, el `calibration_id` del chunk manda sobre el de la sesión. Sin calibración: `skipped` con `no_calibration`; caducada: `calibration_expired`.

### Síntomas

`/assess` calcula features de síntomas de siete días a partir de los autoinformes y el contexto diario del astronauta hasta `as_of`; los valores ausentes figuran como datos faltantes. La baseline calcula los mismos agregados para cada sesión de referencia hasta el final de su ventana (o su fecha de cálculo si no existe), con la gravedad de la sesión. El backend aporta los informes de esas ventanas. Los agregados son provisionales y no son umbrales clínicos.

## Configuración

Variables de entorno (o un `.env`), todas opcionales:

| Variable | Por defecto | Significado |
|---|---|---|
| `SPINE_ENGINE_HOST` / `SPINE_ENGINE_PORT` | `127.0.0.1` / `8001` | dónde escucha (la imagen Docker pone el host a `0.0.0.0`) |
| `SPINE_ENGINE_SCALE_FLOOR` | `0.05` | suelo de la escala del z-score, como fracción de la mediana, cuando MAD y desviación son ~0 |
| `SPINE_ENGINE_CUSUM_K` / `_H` / `_CLIP` / `_MIN_POINTS` | `0.5` / `4.0` / `3.0` / `3` | tolerancia, umbral, tope por punto e histórico mínimo de CUSUM |
| `SPINE_ENGINE_KALMAN_PROCESS_VAR` / `_MEASUREMENT_VAR` / `_THRESHOLD` / `_PERSIST` / `_MIN_POINTS` | `0.05` / `1.0` / `1.0` / `3` / `3` | filtro de Kalman de nivel local |
| `SPINE_ENGINE_STRETCH_RECOVERY_WAIT_S` | `120` | espera de recuperación del cordón en segundos cuando falta `stretch_calibration.recovery_wait_s`; PROVISIONAL hasta #46 |
| `SPINE_ENGINE_STRETCH_RECOVERY_FRACTION` | `0.1` | fracción provisional del pico de repetición usada como umbral de recuperación; configurable hasta #46 |

## Tests

```bash
uv run pytest engine
```
# Triaje declarativo

El motor incluye una política por defecto **provisional**, `src/spine_engine/triage-policy.json` (empaquetada con el motor, cargada por `PolicyConfig.default()`). Aguado todavía no ha entregado las reglas clínicas: cada regla y corte de abajo lleva un `source` que empieza por "provisional, pendiente de Aguado" y nada de ello es una validación clínica. `SPINE_ENGINE_TRIAGE_POLICY_FILE` puede apuntar opcionalmente a una política JSON que sustituye por completo a la por defecto; `stub_pipeline` mantiene los modelos nulos (nivel siempre verde).

Comportamiento: un riesgo igual o superior a `red_cutoff` da nivel de modelo rojo, igual o superior a `amber_cutoff` ámbar, en otro caso verde (los cortes son inclusivos). Los datos incompletos elevan el nivel global a ámbar, y las banderas rojas no reconocidas exigen escalado inmediato y elevan el nivel a rojo (estas dos reglas integradas son `RULE_UNKNOWN_RED_FLAG` y `RULE_INCOMPLETE_DATA`). Se ignoran los informes posteriores a `as_of`. Una feature ausente también eleva a ámbar su dominio identificable.

El JSON admite `amber_cutoff` y `red_cutoff` juntos (inclusivos, entre 0 y 1), `unknown_flag_urgency`, un `source` opcional y una lista `rules`. Cada regla contiene `rule_id`, `datum` (`risk`, `deviation`, `self_report` o `red_flag`), `key`, `operator`, `threshold`, `level`, `domain`, `recommendations`, `urgency` opcional y `source` opcional; para banderas la urgencia es obligatoria. Las claves de informes son `pain_nrs`, `pain_nrs_worst_24h` y `stiffness_nrs`. Las reglas de ejemplo ficticias viven solo en los tests.

Cortes por defecto: `amber_cutoff` 0.3, `red_cutoff` 0.8, `unknown_flag_urgency` `immediate`. Son marcadores de posición: los cortes reales de riesgo global para los niveles de triaje deben decidirse con criterio clínico y, hasta entonces, cualquier configuración de cortes es provisional.

| Regla | Datum | Nivel | Urgencia | Recomendaciones | Base provisional |
|---|---|---|---|---|---|
| `RULE_RED_FLAG_BLADDER_OR_BOWEL` | red_flag `bladder_or_bowel_dysfunction` | rojo | immediate | `REC_CONTACT_FLIGHT_SURGEON` | la disfunción de esfínteres es un signo de alarma clásico de síndrome de cola de caballo; cualquier informe escala de inmediato |
| `RULE_RED_FLAG_SADDLE_NUMBNESS` | red_flag `saddle_numbness` | rojo | immediate | `REC_CONTACT_FLIGHT_SURGEON` | la anestesia en silla de montar es un signo de alarma clásico de síndrome de cola de caballo; cualquier informe escala de inmediato |
| `RULE_RED_FLAG_LEG_WEAKNESS` | red_flag `leg_weakness` | rojo | immediate | `REC_CONTACT_FLIGHT_SURGEON` | el déficit motor sugiere compromiso significativo de raíz nerviosa o médula; se trata como los signos de cola de caballo |
| `RULE_RED_FLAG_FEVER_WITH_BACK_PAIN` | red_flag `fever_with_back_pain` | rojo | immediate | `REC_CONTACT_FLIGHT_SURGEON` | la fiebre con dolor de espalda puede indicar infección espinal; se trata como una emergencia pendiente de revisión clínica |
| `RULE_RED_FLAG_TRAUMA` | red_flag `trauma_event` | rojo | priority | `REC_CONTACT_FLIGHT_SURGEON` | un evento traumático con síntomas de espalda puede implicar fractura o lesión; revisión prioritaria, nivel de urgencia que fijará Aguado |
| `RULE_RED_FLAG_RADIATING_LEG_PAIN` | red_flag `radiating_leg_pain` | ámbar | priority | `REC_CONTACT_FLIGHT_SURGEON` | el dolor radicular sugiere irritación de la raíz nerviosa; marcado para revisión médica prioritaria sin forzar rojo |
| `RULE_RED_FLAG_NUMBNESS_OR_TINGLING` | red_flag `numbness_or_tingling` | ámbar | priority | `REC_CONTACT_FLIGHT_SURGEON` | los síntomas sensitivos sugieren afectación nerviosa; marcado para revisión médica prioritaria sin forzar rojo |
| `RULE_RED_FLAG_SEVERE_NIGHT_PAIN` | red_flag `severe_night_pain` | ámbar | priority | `REC_CONTACT_FLIGHT_SURGEON` | el dolor nocturno no mecánico es una bandera roja convencional de patología grave; marcado para revisión prioritaria |
| `RULE_SELF_REPORT_PAIN_SEVERE` | self_report `pain_nrs` >= 7 | ámbar | - | `REC_CONTACT_FLIGHT_SURGEON` | un NRS de dolor de 7 o más en la escala 0-10 es la convención habitual para dolor intenso; corte pendiente de confirmar |

Todos los códigos de recomendación reutilizan ids que la UI ya renderiza. Lo que Aguado debe confirmar: los cortes de riesgo, el nivel y la urgencia de cada bandera roja (en particular `trauma_event` y las tres banderas ámbar), el umbral de dolor, si hacen falta más reglas (basadas en desviación o en riesgo) y más códigos de recomendación, y el texto de cada recomendación.
