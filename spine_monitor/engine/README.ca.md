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
| `POST /features` | `FeaturesRequest` (dades crues de la sessió) | `FeatureSet` | `features` buit amb les metadades de la sessió, fins que arribi l'extracció |
| `POST /baseline` | `BaselineRequest` (feature sets de les sessions de referència) | `Baseline` | real |
| `POST /assess` | `AssessRequest` | `Assessment` | comparació real amb la baseline; model de risc i triatge nuls (nivell sempre verd) |
| `POST /simulate` | `SimulateRequest` | `SimulateResponse` | stub: no aplica `overrides`, `reference` i `scenario` són la mateixa avaluació |

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
| `FeatureExtractor` | `NullFeatureExtractor` | dades crues a features (la real arriba a #15) |

Els detectors només veuen una sèrie de z-scores: no saben quina mètrica és, així que no depenen del model teòric. `Deviation` porta el z-score, la gravetat de referència i si el canvi és sostingut i en quin sentit; el model de risc ho llegeix d'allà.

## Configuració

Variables d'entorn (o un `.env`), totes opcionals:

| Variable | Per defecte | Significat |
|---|---|---|
| `SPINE_ENGINE_HOST` / `SPINE_ENGINE_PORT` | `127.0.0.1` / `8001` | on escolta (la imatge Docker posa el host a `0.0.0.0`) |
| `SPINE_ENGINE_SCALE_FLOOR` | `0.05` | sòl de l'escala del z-score, com a fracció de la mediana, quan MAD i desviació són ~0 |
| `SPINE_ENGINE_CUSUM_K` / `_H` / `_CLIP` / `_MIN_POINTS` | `0.5` / `4.0` / `3.0` / `3` | tolerància, llindar, topall per punt i històric mínim de CUSUM |
| `SPINE_ENGINE_KALMAN_PROCESS_VAR` / `_MEASUREMENT_VAR` / `_THRESHOLD` / `_PERSIST` / `_MIN_POINTS` | `0.05` / `1.0` / `1.0` / `3` / `3` | filtre de Kalman de nivell local |

## Tests

```bash
uv run pytest engine
```
