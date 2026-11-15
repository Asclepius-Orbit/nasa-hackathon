# Decision on the current values and scope

On 14 November 2026 the project decided to adopt the existing values, rules and behaviour as final, closing the work of issues #38, #46, #53 and #130 with the current scope. This is a project decision; it does not claim new measurements or clinical validation by the clinical reviewer or the hardware lead. Historical references to provisional or pending values must be read in light of this decision.

The engine keeps the directions and ranges available in the catalog, the report thresholds and the current operational rules and cut-offs. The historical statuses and sources in the catalog and the policy describe their provenance, not additional validation. Cord recovery defaults to 120 s and a fraction of 0.1; a calibration may specify a different wait. The gateway's MVC capture lasts 5 s by default and uses a 250 ms RMS window. The simulated mission declares three attempts; the gateway does not automatically run three captures.

Closing the scope adds no features. The telemetry and window integration keeps the capabilities and limitations documented in `spine_monitor/docs/measured-window-integration.md`: there is no continuous scheduling of live windows and no acquisition of an independent IMU orientation reference in the gateway. The length estimate remains calculated, not a measurement or a clinical validation.

The range of `imu.{spine_level}.{anatomical_plane}.rom_deg` was removed from the deviation catalog (`risk_direction` `unspecified`, null ranges) because the lumbar IMU limit is now evaluated with the measured-window thresholds: amber from 35, red from 42, and values below 25 unclassified (`spine_monitor/engine/src/spine_engine/measurements.py`).
