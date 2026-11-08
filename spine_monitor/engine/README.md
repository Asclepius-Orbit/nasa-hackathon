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
| `POST /features` | `FeaturesRequest` (raw session data) | `FeatureSet` | `features` from the registered families: `exercise` since #41 and `emg` (amplitude) since #42; the IMU and strap families follow in #43-#45 |
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
| `FeatureExtractor` | `CompositeFeatureExtractor` | raw data to features: runs the registered feature families (`exercise`, #41; `emg` amplitude, #42; IMU and strap in #43-#45) |

The detectors see only a series of z-scores: they do not know which metric it is, so they do not depend on the theoretical model. `Deviation` carries the z-score, the reference gravity and whether the change is sustained and in which direction; the risk model reads it from there.

## Feature extraction

`POST /features` runs `CompositeFeatureExtractor` over an `ExtractorRegistry` of feature families (`FeatureFamilyExtractor`: a `family` name, the catalog `feature_ids` it declares and `extract(context) -> FamilyResult` with `computed` and `skipped` features). `default_registry()` registers the `exercise` (#41) and `emg` amplitude (#42) families, so the repetition and EMG amplitude metrics are computed today; the metrics of the other sensors arrive family by family (#43-#45). `NullFeatureExtractor` stays in `stubs.py` for tests.

Before the families run, the engine prepares an `ExtractionContext` once:

- **Chunks:** only chunks of the session and of declared streams are used. They are ordered by (stream, `boot_id`, `seq`); the first copy of a duplicate wins; each `boot_id` is an independent segment (samples of two boots are never joined) and a restart is reported as a gap. Lost chunks (`seq` jumps) and `dropped_samples` count as missing samples.
- **Clock:** `device_ms` becomes UTC per `boot_id` with a least-squares line over its `clock_syncs` (one anchor means a fixed offset). A boot without anchors has no UTC: its data does not feed any feature (`no_data`). `window` is the UTC span of the mapped chunks, omitted when there is no clock.
- **Calibration:** the chunk's `calibration_id` wins over the session's; it is looked up in `calibrations` by `stream_id` (and kind). No match: `no_calibration`; `valid_until` before the end of the window: `calibration_expired`. Never a 422.
- **Quality:** `score = coverage x (1 - saturated fraction)`, `assessed_by = "engine"`, with flags for gaps (`packet_gap`), saturation (`saturated_adc`, `clipping`), an expired calibration (`out_of_calibration`) and, if `clock_uncertain_ms` is configured, `clock_uncertain`. Saturation is only judged where the data allows it (ADC bits, IMU ranges); there are no clinical thresholds.

The composite fills `unit` from the feature catalog and the quality over the streams each feature declared using. A feature id outside the catalog is a programming error of the family and raises `UnknownFeatureIdError`. What cannot be computed goes to `skipped` with a reason: `stream_missing` (declared stream without chunks), `no_calibration`, `calibration_expired` and `no_data` (coverage 0, no UTC or a non-finite value) among others; the request is never rejected for being incoherent. `features` and `skipped` are sorted, so the output does not depend on the registration order.

### Repetitions and the `exercise` family

`exercise.rep_count`, `exercise.rep_duration_s_mean` and `exercise.rep_duration_cv_pct` (CV in %, sample standard deviation with n-1) need at least 2 valid repetitions. With fewer, all three are `skipped` with `too_few_repetitions`.

`RepetitionService` resolves the repetition windows (`RepWindow`): start, end, the active phase (rep_start→rep_end) and the rest (rep_end→next rep_start). The last repetition only has rest if a `rest_end` event follows it. `default_registry()` creates a single service and passes it to the families, so the other families (the EMG one since #42; IMU and strap in #43-#45) reuse it.

The source is, in this order:

1. The session's `rep_start`/`rep_end` events (recorded by the `mark_event` command), sorted by time and paired in order. Orphans, duplicates and windows where end <= start are discarded.
2. If no valid window remains, an IMU fallback that uses only the gyroscope (in microgravity the accelerometer does not see gravity): lobes of opposite-sign angular velocity on the dominant axis, per `boot_id`, on the UTC clock. Without signal it returns nothing.
3. If neither yields anything, there are no repetitions.

Quality: the event-based features evaluate the common quality over all declared streams; the IMU-fallback features evaluate it over the IMU streams. Without chunks that have a clock anchor, the result is `no_data`.

### EMG amplitude and the `emg` family

For every declared EMG stream with a muscle and a side, `emg.{muscle}.{side}.rms_mv` is the RMS of the active-phase samples of all repetitions and `rest_rms_mv` the RMS of their rest windows. `rms_pct_mvc` divides `rms_mv` by `mvc_rms_mv` of the stream's `emg_mvc` calibration; `peak_pct_mvc` is the highest windowed RMS (window = the calibration's `window_ms`, never crossing from one repetition or `boot_id` to another) over the same MVC. `emg.{muscle}.symmetry_index_pct` is 100·|L-R| / ((L+R)/2) over the two `rms_pct_mvc`. The amplitude needs no raw signal: the computation is the same; the value matches for raw and rectified, not for the envelope. `n` is the number of repetitions that contributed samples. The MVC is only used when it belongs to the stream's muscle and side and was measured on a signal kind compatible with every chunk of the stream (raw and rectified with each other, envelope only with envelope); otherwise the %MVC features and the symmetry are `skipped` with `no_calibration`, while `rms_mv` and `rest_rms_mv` are still computed.

What cannot be computed goes to `skipped`: `no_calibration` / `calibration_expired` for what needs the MVC (the symmetry needs both sides; a missing one wins over an expired one), `too_few_repetitions` without repetitions, `no_data` without samples in the windows (or with an empty mean in the symmetry, or a rest without a rest window), and `stream_missing` for a declared stream without chunks or a muscle with a single declared side.

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
