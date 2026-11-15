---
name: physiology-engine
description: Use when changing or adding a model, signal, feature, curve, fault, scenario or scoring in `physiology` or `engine`, or when a change alters the simulated output or the golden snapshot.
---

# physiology-engine

Guide for touching the simulator (`spine_monitor/physiology`) and the engine (`spine_monitor/engine`). Every code change follows `tdd-workflow`; this skill only adds what is specific to the domain. For Hypothesis invariants also load `property-based-testing` (see `.claude/skills/ROUTING.md`).

## 1. Determinism: same config + same seed = same bytes

- All randomness comes from `spine_physiology.rng` (`generator(seed, *keys)`, `RngTree`). Branches by keys `(astronaut_id, day, slot, stream_id, purpose)`; each branch is independent, so adding a new branch does not shift the others. A stochastic function receives an `np.random.Generator` as a parameter (like `faults.py` and `device.py`), never creates it itself.
- Forbidden in `physiology/src`: global `np.random.*`, `random`, `datetime.now()`, `time.time()`. The only wall clocks today are the live gateway (`gateway/app.py`) and `sinks/pacing.py` (injectable `clock`). Simulated time comes from the config and the day index.
- A new UUID or `boot_id` is derived with `rng.derive_uuid` / `derive_boot_id`, not with `uuid4()`.
- Do not depend on set iteration order or on the speed or the sink: `day_messages` and `session_messages` must give the same as `iter_messages()` (`physiology` README, Reproducibility section).
- In `engine`, `created_at`/`computed_at` use `datetime.now(UTC)` (`baseline/builder.py`, `stubs.py`): it is metadata, it does not enter calculations or test comparisons. If you need a clock in logic, inject it.
- Clinical or physiological numeric values: never in code. They come from the mission YAML (`physiology/configs/example-mission.yaml`, placeholder numbers) and are validated in `config.py` (Pydantic, unknown keys rejected).

## 2. Units: pint at the edges, floats in the loops

- `pint` is in the dependencies of `physiology/pyproject.toml` but **is not used in any `.py` today**: units live in the names (`length_mm`, `rate_hz`, `_ohm`, `_s`, `_ms`). Before introducing it, confirm with the maintainer where.
- If you use it: a single `UnitRegistry` per module, conversion when reading config and when writing payloads; inside the synthesis (numpy arrays, per-sample loops) only `float`/`ndarray` in a documented base unit. Never `Quantity` inside a hot loop or inside numpy arrays.
- Contract payloads carry the unit in the feature catalog (`contracts/catalog`); do not invent another.

## 3. Adding or changing a model, feature, fault or scenario

1. Read the neighbouring module and copy its pattern (`signals/*.py`, `curves.py`, `faults.py`, `state.py`, `truth.py`).
2. Red test first. Choose the marker (`tdd-workflow` table): `unit` for the concrete case, `property` for the invariant, `golden` only for the full output.
3. New parameters: required in the Pydantic model in `config.py` and in `example-mission.yaml`; no default values in code.
4. **New feature**: it must exist in the `contracts/catalog` catalog; the ground truth (`truth.expected_features`) and the engine's extraction must match the noiseless model (pattern from `physiology/tests/test_truth.py`).
5. **New scenario**: today a scenario is a YAML config plus `segments` (preflight 1 g, inflight microgravity, postflight) and `gen.resimulate(ref, gravity)` for the A/B pair. Reuse that before creating another mechanism. The engine's `POST /simulate` applies `overrides` to the last value of each feature (`simulation.py`); `project_days` is not applied yet.
6. **New fault** (`faults.py`): it must be recorded in the ground truth (`quality_faults`, `lost_chunks`, `device_events`) and with its contract `QualityFlag`.
7. If a contract payload, field or enum changes, stop and load `contract-first` before editing the model.

## 4. Invariants to check

Reference property tests: `physiology/tests/test_properties_curves.py`, `test_properties_rng.py`, `engine/tests/test_properties_scoring.py`. Strategies without `assume`/`filter` (build the valid input, like `curves()`), bounded floats with no NaN/inf unless you are testing exactly that.

- Rng: same branch = same stream; different branches = different streams (including integer vs text `1`/`"1"`); valid UUID v4; `boot_id` in 32 bits.
- Curves: pass through their knots, stay within the knot range, vectorised `values` = scalar call.
- Scoring and detectors: finite z-score, the median scores 0, monotonic in the value; an isolated spike is never a sustained change.
- Physiological and technical limits: latents in `[0, 1]` except `spine_elongation` (mm); ADC counts within range; `seq`/`t0_device_ms` return to 0 after a restart; a day regenerated separately gives the same payloads. The limits are those of the config, not test constants.
- Coverage of edges Hypothesis does not find on its own: add an explicit `@example` (see `test_different_branches_give_different_streams`).

## 5. Golden snapshots

- One exists: `physiology/tests/golden/tiny-mission.json`, generated by `physiology/tests/test_golden_mission.py` (mini mission, fixed seed, counts per type + sha256 of all messages, floats rounded to 4 decimals).
- Use it only to detect unintended changes of the full output. Do not use it to test logic: that is `unit` or `property`.
- If it fails: first find out **why** the output changed. An intentional change (model, rng branch, message order, contract field) that can be explained in one sentence: regenerate. Not explainable: it is a determinism bug, fix it.
- Regenerate, from `spine_monitor`: `UPDATE_GOLDEN=1 uv run pytest physiology/tests/test_golden_mission.py -q -p no:randomly`; run again without the variable; review the JSON diff (`messages`, `by_kind` change or only `sha256`) and explain it in the commit message and the PR. Commit it together with the change that causes it.
- Do not add new snapshots for convenience; one per end-to-end behaviour.

## 6. Commands (from `spine_monitor`)

- Fast loop: `uv run pytest physiology/tests/<file> -q -p no:randomly`, `uv run pytest engine -q`.
- Only properties or golden: `uv run pytest physiology engine -m property` / `-m golden`.
- Full gate (Docker, run by the maintainer or CI): see `tdd-workflow`.

## Warning signs — STOP

| Thought | Reality |
|---|---|
| "I'll use `np.random.normal` / `random` for this, it's just once" | It breaks reproducibility. Ask for a `Generator` from `rng` per branch. |
| "I'll put `datetime.now()` in the signal" | Simulated time comes from config and day. The wall clock only lives in the gateway and in `pacing`. |
| "I'll regenerate the golden so it passes" | Without understanding the diff, you hide a bug. Explain the change or fix it. |
| "I'll hardcode this physiological threshold, it's reasonable" | Every clinical number comes from the YAML and is validated in `config.py`. |
| "I'll wrap everything in pint `Quantity`" | Pint at the edges; inside, floats/ndarrays in a base unit. |
| "A new rng branch affects nothing" | Only if the key is new. Reusing or reordering keys changes the bytes. |
| "I'll change the payload and then adjust the contract" | Contract first: `contract-first` skill. |
| "The property test passes with `assume(...)`" | Build valid inputs; an `assume` that discards almost everything proves nothing. |
