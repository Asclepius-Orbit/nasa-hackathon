**English** · [Español](README.es.md) · [Català](README.ca.md)

# spine-engine

Stateless predictive engine: it takes the astronaut profile, a baseline, feature sets, self-reports and daily context (`AssessRequest`) and returns an `Assessment`. Features that cannot be compared with the baseline are reported as missing in the assessment's completeness, never as green by default. It defines the `BaselineStrategy`, `RiskModel` and `TriagePolicy` interfaces, an `AssessmentPipeline` that wires them together, and null stubs (`stub_pipeline()`).

## Running it

From the `spine_monitor` workspace:

```bash
uv run spine-engine                      # http://127.0.0.1:8001
docker compose -f ../app/docker-compose.yml up --build engine
```

The contract is `contracts/openapi.yaml`; bodies and errors are the JSON Schemas in `contracts/schemas/v1/engine/`. The engine listens on localhost only.

| Endpoint | Body in | Body out | Today |
|---|---|---|---|
| `GET /health` | - | `{"status": "ok"}` | real |
| `POST /features` | `FeaturesRequest` (raw session data) | `FeatureSet` | empty `features` with the session metadata, until the extraction lands |
| `POST /baseline` | `BaselineRequest` (feature sets of the reference sessions) | `Baseline` | real |
| `POST /assess` | `AssessRequest` | `Assessment` | real baseline comparison; null risk model and triage (level always green) |
| `POST /simulate` | `SimulateRequest` | `SimulateResponse` | stub: `overrides` are not applied, `reference` and `scenario` are the same assessment |

Errors are problem details (`application/problem+json`, `engine/error.schema.json`): `422` with `invalid_request` (with a JSON Pointer per problem), `unsupported_schema_version` or `insufficient_data`, and `500` with `internal`. Request bodies are checked against the JSON Schema as well as Pydantic, so the conditional rules are enforced.

```bash
curl -s -X POST http://127.0.0.1:8001/assess \
  -H 'content-type: application/json' \
  --data-binary @contracts/examples/valid/engine/assess-request.json
```

### Mocking it

The examples in `contracts/examples/valid/engine/` are valid requests for every endpoint, and `contracts/examples/valid/domain/` holds valid responses (`assessment.amber.json`, `baseline.json`, `feature-set.*.json`). A mock only needs to return those for the matching route and answer errors with `engine/error.json`.

## Personal baseline and deviations

`POST /baseline` builds one entry per feature + protocol + gravity environment (mean, standard deviation, median and MAD) from the reference sessions, normally pre-flight in 1 g. A feature seen in a single session still gets an entry (`n` says how far to trust it). The caller stores the baseline and sends it with every `/assess`.

`/assess` compares the latest value of each feature with the entry of its own gravity, or with Earth's when there is none. Everything is injected, so swapping a piece means writing another class, not editing the existing ones:

| Interface | Default | What it does |
|---|---|---|
| `DeviationScorer` | `RobustZScorer` | `(x - median) / (1.4826 * MAD)`; falls back to the standard deviation and then to a floor proportional to the median |
| `ChangeDetector` | `CompositeDetector` of `CusumDetector` + `KalmanDetector` | sustained change over the history (same protocol and gravity); a single spike never counts |
| `VotePolicy` | `AllAgreeVote` | the detectors must agree (AND), in the same direction |
| `BaselineBuilder` | `StatisticalBaselineBuilder` | builds the baseline |
| `FeatureExtractor` | `NullFeatureExtractor` | raw data to features (real one in #15) |

The detectors see only a series of z-scores: they do not know which metric it is, so they do not depend on the theoretical model. `Deviation` carries the z-score, the reference gravity and whether the change is sustained and in which direction; the risk model reads it from there.

## Configuration

Environment variables (or a `.env`), all optional:

| Variable | Default | Meaning |
|---|---|---|
| `SPINE_ENGINE_HOST` / `SPINE_ENGINE_PORT` | `127.0.0.1` / `8001` | where it listens (the Docker image sets the host to `0.0.0.0`) |
| `SPINE_ENGINE_SCALE_FLOOR` | `0.05` | z-score scale floor, as a fraction of the median, when MAD and deviation are ~0 |
| `SPINE_ENGINE_CUSUM_K` / `_H` / `_CLIP` / `_MIN_POINTS` | `0.5` / `4.0` / `3.0` / `3` | CUSUM tolerance, threshold, cap per point and minimum history |
| `SPINE_ENGINE_KALMAN_PROCESS_VAR` / `_MEASUREMENT_VAR` / `_THRESHOLD` / `_PERSIST` / `_MIN_POINTS` | `0.05` / `1.0` / `1.0` / `3` / `3` | local-level Kalman filter |

## Tests

```bash
uv run pytest engine
```
