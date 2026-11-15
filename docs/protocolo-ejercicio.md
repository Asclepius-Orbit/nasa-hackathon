# Standardised exercise protocol and sensor placement

> The current values and scope are considered final by project decision. See [the decision](decision-valores-protocolo.md); the historical "pending" marks need no further work to close the linked issues.

Source: a comment on issue #12 by the clinical reviewer. The values live in the contract (`spine_monitor/contracts/catalog/protocol-catalog.json`, examples under `examples/valid/`) and in the simulator's example mission (`spine_monitor/physiology/configs/example-mission.yaml`). This document summarises them; if they differ, the contract wins.

## Protocols (`protocol_id`)

| `protocol_id` | What it is |
|---|---|
| `lat_pulldown_standing_v1` | Standing lat pulldown with an elastic band |
| `spine_length_rest_v1` | Passive `spine_check`, standing, right before (`phase: pre`) and right after (`phase: post`) the exercise; the session carries a `related_session_id` pointing to the exercise session |
| `spine_resistance_straight_v1` | Resistance with the back straight; personal reference on Earth and follow-up in flight |
| `spine_resistance_max_flexion_v1` | Resistance with the back in maximum flexion; personal reference on Earth and follow-up in flight |

## Exercise (`lat_pulldown_standing_v1`)

- Posture: standing. Pronated grip. Moderate pace.
- Load: green elastic band, ~25 kg nominal (15-35 kg depending on elongation).
- Volume: 4 sets × 15 repetitions = 60 repetitions in about 5 minutes (`planned_sets`, `reps_per_set`, `planned_reps`/`total_reps` = 60). `rep_index` counts across the whole session (1..60); the `set_start`/`set_end` events carry `set_index`.
- Recovery between sets: very short. `rest_between_sets_s` = 30 s is a nominal value. **Pending clinical review.**

## Sensor placement

| Sensor | Placement | Identifier |
|---|---|---|
| EMG | One module on each latissimus dorsi, over T11-L2, ~10 cm between electrodes | `emg_lat_left`, `emg_lat_right` |
| Ground (GND) | Iliac crest | `placement.ground.location = iliac_crest` |
| IMU | T9 | `imu_t9` (`spine_level: t9`) |
| Cord | From C3-C4 to L5-S1, ~65 cm at rest (`full_spine`) | `stretch_spine`; features `stretch.full_spine.*` |

`placement` fields for EMG: `electrode_spacing_cm`, `span` (T11→L2), `ground`.

## Conductive cord

- Elongation comes from the change in resistance relative to the rest resistance `R0`, through a **measured** curve. The cord is not linear and takes 1-2 minutes to recover after being stretched: `R0` is taken after that wait.
- **350 Ω is the resistance of one inch of cord at rest, not a 2.54 cm deformation.** It is not used as a length conversion factor.
- The clinical reviewer and the hardware lead reported 4,500 Ω with the back straight and 5,650 Ω at maximum flexion, with 50 cm between the ohmmeter contacts. These are informative references, not clinical thresholds; each astronaut is compared with their own Earth reference per posture.
- In a separate bench test the whole cord was stretched to each length and measured three times with the contacts 10 cm apart:

| Total length | 1 | 2 | 3 |
|---|---:|---:|---:|
| 100 cm | 630 Ω | 780 Ω | 795 Ω |
| 110 cm | 1,020 Ω | 1,165 Ω | 1,125 Ω |
| 120 cm | 1,110 Ω | 1,255 Ω | 1,260 Ω |
| 130 cm | 1,270 Ω | 1,320 Ω | 1,300 Ω |
| 140 cm | 1,350 Ω | 1,355 Ω | 1,350 Ω |

The total length in the table and the distance between contacts are different quantities. As an experimental approximation, the three resistances at each length are averaged, multiplied by the reference distance between contacts divided by 100 mm (a factor of 5 for the reported 500 mm), and interpolated piecewise, only within the measured range. To estimate the length between clamps, use `reference length × current equivalent total length / equivalent total length when straight`; the estimated change refers to the individual straight posture measured on Earth. The factor assumes uniform resistance and negligible contact resistance; it also uses the fixed reference spacing to scale the resistance even though the spacing may change when the cord is stretched. Carrying the length ratio over assumes uniform deformation and clamps that stay fixed to the material. These are unvalidated hypotheses: the table is not a curve measured on the back setup and the estimate is not a clinical measurement. With no personal straight reading, or with any resistance outside the interval, there is no estimate; nothing is extrapolated. The original readings and the setup are in `contracts/catalog/cord-measurements.json`.

The 650 mm rest length and the example calibration curve remain provisional; a calibration applicable to the real placement and the recovery time still need to be measured or validated (#106, #46). Capture and periodic follow-up of both postures remain in #122.

Application integration (cord sessions): `spine_resistance_straight_v1` and `spine_resistance_max_flexion_v1` are offered in the capture flow as two independent sessions. The Earth reference is the latest valid preflight measurement (1 g); the follow-up is the latest valid measurement in a different gravity environment. "Valid" means a completed session and feature quality ≥ `cord_min_quality_score` (0.5 by default, provisional, pending clinical review). The cadence is configurable (`cord_follow_up_interval_days`, 7 by default as an example, not a clinical guideline; pending clinical review): in preflight the reference is due until a valid one exists; in flight it is due when there is no follow-up or the latest one is at least that many days old. The view `GET /ui/crew/{id}/views/cord-protocols` and the tasks `TASK_CORD_STRAIGHT` / `TASK_CORD_MAX_FLEXION` expose this. The estimate (`POST /stretch-estimate` of the engine) is always shown labelled as calculated, with its assumptions, never as a measured length or clinically validated, and it plays no part in triage. It is only computed for the straight back (50 cm between contacts); at maximum flexion the distance between contacts is not measured, so there is no estimate (pending clinical review).

Sources on the material's behaviour: [Adafruit 519 cord manufacturer](https://www.adafruit.com/product/519) (non-linear response and unit-to-unit variation) and [study on conductive elastomer sensors](https://advanced.onlinelibrary.wiley.com/doi/full/10.1002/admt.201700284) (resistivity dependence and hysteresis). They do not validate the experimental model or replace calibration of the setup used.

## MVC

The values and behaviour the project already implements are adopted as final: by default the gateway records a 5 s capture (configurable) and computes the MVC as the maximum RMS over a 250 ms window. The simulated mission declares three attempts; the gateway does not automatically run three captures. The features `emg.*.rms_pct_mvc` and `emg.*.peak_pct_mvc` require `emg_mvc_calibration`.

## Hardware status (#13)

The protocol declares two EMG channels, but the real firmware and gateway publish one (`emg_lat_left`) until the hardware is confirmed with the hardware lead. The serial frame format does not change.

## Values and where they are changed

The project's current values are considered final by project decision. The table below shows where each one is maintained and checked; the decision does not imply that every value has been measured or clinically validated. Changing the source propagates the value only when the "Updated by hand" column is empty; otherwise the list is closed and a test fails if a copy diverges.

## Where each value is changed

| Value | Single source | Updated by hand (closed list) | Test that fails if it diverges |
|---|---|---|---|
| Rest between sets (30 s), sets and repetitions (4 × 15), band (green, 25 kg) | `protocol-catalog.json` | `example-mission.yaml` (`planned_sets`, `reps_per_set`, `rest_between_sets_s`, `load`); `app/ui/src/main/sessions/protocols.ts` (band, sets, reps; it does not carry the rest); mocks in `app/ui/src/renderer/data/mock/` | `contracts/tests/test_catalog.py` (mission↔catalogue cross-check); `protocols.test.ts`; `protocol-values.test.ts` (mocks) |
| Rest at the end (`rest_seconds`, 120 s) | `protocol-catalog.json` | UI mocks; the UI's "2 min" text is derived from this value (`strings/session.ts`) | `protocol-values.test.ts`; `contracts/tests/test_catalog.py` |
| Pace (`rep_duration_s` 3.0 s, `rest_between_reps_s` 0.6 s), `gap_s` (30 s) and `rom_deg` (30°) | `protocol-catalog.json` | `example-mission.yaml` (`rep_duration_s`, `rest_between_reps_s`, `rom_deg`, `bracket.gap_s`) | `contracts/tests/test_catalog.py`; `physiology/tests/test_protocol_sets.py` |
| Cord rest length (650 mm; 666 mm for the second astronaut) | `example-mission.yaml` (per astronaut, simulator) | `contracts/catalog/calibration-defaults.json` (the gateway reads it from there; `SPINE_GATEWAY_STRETCH_REST_LENGTH_MM` changes it on the real device); `calibration.stretch.json`; `features-request.json`; gateway, contracts and firmware READMEs | `engine/tests/test_features_stretch_simulator.py` reads the mission; `contracts/tests/test_calibration_defaults.py` cross-checks the mission with the file (AST-01 only, 650 mm) |
| Cord curve | `contracts/catalog/calibration-defaults.json` (4-point curve and rest 6012 ohm / 650 mm); the gateway scales it to the measured R0 and `SPINE_GATEWAY_STRETCH_CURVE` replaces it | `example-mission.yaml` (`signals.stretch.curve`) | `contracts/tests/test_calibration_defaults.py`; `gateway/tests/test_calibration.py`; `physiology/tests/test_signals_stretch.py` |
| Cord recovery wait (120 s) and recovery fraction (0.1) | `engine/src/spine_engine/settings.py` (engine defaults); the calibration's `recovery_wait_s` wins if present | `example-mission.yaml` (`recovery_wait_s`), `calibration.stretch.json`, `features-request.json`, engine README | `engine/tests/test_settings.py` (pins 120 and 0.1 and that `StretchFamily` uses the same value) |
| MVC window (250 ms) and `n_trials` (3) | `contracts/catalog/calibration-defaults.json` (`emg_mvc.window_ms`; `n_trials` only in the mission) | `example-mission.yaml` (`emg_mvc`); `SPINE_GATEWAY_EMG_MVC_WINDOW_MS` (gateway); examples `calibration.emg-mvc*.json`; gateway READMEs | `contracts/tests/test_calibration_defaults.py`; `gateway/tests/test_settings.py` |
| Electrode spacing, span, GND (10 cm, T11-L2, iliac crest) and IMU (T9) | `session.exercise.json` / `example-mission.yaml` | `protocols.ts`; `strings/codes.ts` (text) | `protocols.test.ts` (`streams` only) |
| Placement texts and UI labels | `app/ui/src/renderer/strings/` | all by hand, and READMEs/firmware | none |
| Range weights, triage cut-offs | `engine/src/spine_engine/risk.py`, `triage-policy.json` | engine README tables | `engine/tests/test_default_triage_policy.py` ("provisional" mark) |

What gets regenerated: `protocol-catalog.json` does not feed the Python models or `v1.ts`; if a schema changes, run `uv run python contracts/scripts/generate_models.py` and `pnpm run gen:types`. What no test covers: the gateway's default values against the mission, and the prose notes in the READMEs.
