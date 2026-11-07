[English](README.md) · **Español** · [Català](README.ca.md)

# spine-engine

Motor predictivo sin estado: recibe el perfil del astronauta, una baseline, conjuntos de features, autoinformes y contexto diario (`AssessRequest`) y devuelve un `Assessment`. Las features que no se pueden comparar con la baseline se declaran como faltantes en la completitud de la evaluación, nunca como verde por defecto. Define las interfaces `BaselineStrategy`, `RiskModel` y `TriagePolicy`, un `AssessmentPipeline` que las junta y stubs nulos (`stub_pipeline()`).

## Cómo arrancarlo

Desde el workspace `spine_monitor`:

```bash
uv run spine-engine                      # http://127.0.0.1:8001
docker compose -f ../app/docker-compose.yml up --build engine
```

El contrato es `contracts/openapi.yaml`; cuerpos y errores son los JSON Schemas de `contracts/schemas/v1/engine/`. El motor solo escucha en localhost.

| Endpoint | Cuerpo de entrada | Cuerpo de salida | Hoy |
|---|---|---|---|
| `GET /health` | - | `{"status": "ok"}` | real |
| `POST /features` | `FeaturesRequest` (datos crudos de la sesión) | `FeatureSet` | `features` vacío con los metadatos de la sesión, hasta que llegue la extracción |
| `POST /baseline` | `BaselineRequest` (feature sets de las sesiones de referencia) | `Baseline` | real |
| `POST /assess` | `AssessRequest` | `Assessment` | comparación real con la baseline; modelo de riesgo y triaje nulos (nivel siempre verde) |
| `POST /simulate` | `SimulateRequest` | `SimulateResponse` | stub: no aplica `overrides`, `reference` y `scenario` son la misma evaluación |

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
| `FeatureExtractor` | `CompositeFeatureExtractor` | datos crudos a features: ejecuta las familias registradas (el registro está vacío hasta #41-#45) |

Los detectores solo ven una serie de z-scores: no saben qué métrica es, así que no dependen del modelo teórico. `Deviation` lleva el z-score, la gravedad de referencia y si el cambio es sostenido y en qué sentido; el modelo de riesgo lo lee de ahí.

## Extracción de features

`POST /features` ejecuta `CompositeFeatureExtractor` sobre un `ExtractorRegistry` de familias de features (`FeatureFamilyExtractor`: un nombre `family`, los `feature_ids` del catálogo que declara y `extract(context) -> FamilyResult` con features `computed` y `skipped`). El registro por defecto está vacío, así que hoy la respuesta es un `FeatureSet` vacío y honesto; las métricas llegan familia a familia. `NullFeatureExtractor` sigue en `stubs.py` para los tests.

Antes de ejecutar las familias, el motor prepara una sola vez un `ExtractionContext`:

- **Chunks:** solo se usan los de la sesión y de streams declarados. Se ordenan por (stream, `boot_id`, `seq`); de un duplicado gana la primera copia; cada `boot_id` es un segmento independiente (nunca se unen muestras de dos arranques) y un reinicio se reporta como hueco. Los chunks perdidos (saltos de `seq`) y los `dropped_samples` cuentan como muestras ausentes.
- **Reloj:** `device_ms` pasa a UTC por `boot_id` con una recta de mínimos cuadrados sobre sus `clock_syncs` (con una sola ancla, un desfase fijo). Un arranque sin anclas no tiene UTC: sus datos no alimentan ninguna feature (`no_data`). `window` es el rango UTC de los chunks mapeados y se omite si no hay reloj.
- **Calibración:** el `calibration_id` del chunk manda sobre el de la sesión; se busca en `calibrations` por `stream_id` (y tipo). Sin coincidencia: `no_calibration`; `valid_until` anterior al final de la ventana: `calibration_expired`. Nunca un 422.
- **Calidad:** `score = cobertura x (1 - fracción saturada)`, `assessed_by = "engine"`, con flags de huecos (`packet_gap`), saturación (`saturated_adc`, `clipping`), calibración caducada (`out_of_calibration`) y, si se configura `clock_uncertain_ms`, `clock_uncertain`. La saturación solo se juzga donde los datos lo permiten (bits del ADC, rangos de la IMU); no hay umbrales clínicos.

El compuesto rellena `unit` desde el catálogo de features y la calidad sobre los streams que cada feature declaró usar. Un id fuera del catálogo es un error de programación de la familia y lanza `UnknownFeatureIdError`. Lo que no se puede calcular va a `skipped` con motivo: `stream_missing` (stream declarado sin chunks), `no_calibration`, `calibration_expired` y `no_data` (cobertura 0, sin UTC o valor no finito), entre otros; la petición nunca se rechaza por incoherente. `features` y `skipped` salen ordenadas, así que el resultado no depende del orden de registro.

## Configuración

Variables de entorno (o un `.env`), todas opcionales:

| Variable | Por defecto | Significado |
|---|---|---|
| `SPINE_ENGINE_HOST` / `SPINE_ENGINE_PORT` | `127.0.0.1` / `8001` | dónde escucha (la imagen Docker pone el host a `0.0.0.0`) |
| `SPINE_ENGINE_SCALE_FLOOR` | `0.05` | suelo de la escala del z-score, como fracción de la mediana, cuando MAD y desviación son ~0 |
| `SPINE_ENGINE_CUSUM_K` / `_H` / `_CLIP` / `_MIN_POINTS` | `0.5` / `4.0` / `3.0` / `3` | tolerancia, umbral, tope por punto e histórico mínimo de CUSUM |
| `SPINE_ENGINE_KALMAN_PROCESS_VAR` / `_MEASUREMENT_VAR` / `_THRESHOLD` / `_PERSIST` / `_MIN_POINTS` | `0.05` / `1.0` / `1.0` / `3` / `3` | filtro de Kalman de nivel local |

## Tests

```bash
uv run pytest engine
```
