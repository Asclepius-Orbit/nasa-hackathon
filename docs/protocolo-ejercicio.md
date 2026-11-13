# Protocolo estandarizado del ejercicio y colocación de sensores

Fuente: comentario de Aguado en la issue #12. Los valores viven en el contrato (`spine_monitor/contracts/catalog/protocol-catalog.json`, ejemplos de `examples/valid/`) y en la misión de ejemplo del simulador (`spine_monitor/physiology/configs/example-mission.yaml`). Este documento los resume; si discrepan, manda el contrato.

## Protocolos (`protocol_id`)

| `protocol_id` | Qué es |
|---|---|
| `lat_pulldown_standing_v1` | Jalón al pecho de pie con banda elástica |
| `spine_length_rest_v1` | `spine_check` pasivo, de pie, justo antes (`phase: pre`) y justo después (`phase: post`) del ejercicio; la sesión lleva `related_session_id` hacia la del ejercicio |

## Ejercicio (`lat_pulldown_standing_v1`)

- Postura: de pie. Agarre prono. Ritmo moderado.
- Carga: banda elástica verde, ~25 kg nominales (15-35 kg según la elongación).
- Volumen: 4 series × 15 repeticiones = 60 repeticiones en unos 5 minutos (`planned_sets`, `reps_per_set`, `planned_reps`/`total_reps` = 60). `rep_index` cuenta sobre toda la sesión (1..60); los eventos `set_start`/`set_end` llevan `set_index`.
- Recuperación entre series: muy corta. `rest_between_sets_s` = 30 s es un valor nominal. **Pendiente de Aguado.**

## Colocación de sensores

| Sensor | Colocación | Identificador |
|---|---|---|
| EMG | Un módulo en cada dorsal ancho, sobre T11-L2, ~10 cm entre electrodos | `emg_lat_left`, `emg_lat_right` |
| Tierra (GND) | Cresta ilíaca | `placement.ground.location = iliac_crest` |
| IMU | T9 | `imu_t9` (`spine_level: t9`) |
| Cordón | De C3-C4 a L5-S1, ~65 cm en reposo (`full_spine`) | `stretch_spine`; features `stretch.full_spine.*` |

Campos de `placement` para EMG: `electrode_spacing_cm`, `span` (T11→L2), `ground`.

## Cordón conductor

- La elongación sale del cambio de resistencia respecto a la resistencia en reposo `R0`, a través de una curva **medida**. El cordón no es lineal y tarda 1-2 minutos en recuperarse tras estirarse: `R0` se toma tras esa espera.
- **350 Ω es la resistencia de una pulgada de cordón en reposo, no una deformación de 2,54 cm.** No se usa como factor de conversión de longitud.
- **Pendiente de Aguado:** hasta que mida el cordón con óhmetro, la longitud en reposo (650 mm) y la curva son valores provisionales, marcados como «PENDIENTE medición Aguado» en los comentarios, los READMEs y el ejemplo de calibración. Cuando haya mediciones se abrirá una issue para actualizarlos.

## MVC

El comentario no define el procedimiento de contracción voluntaria máxima. Las features `emg.*.rms_pct_mvc` y `emg.*.peak_pct_mvc` siguen requiriendo `emg_mvc_calibration` y quedan sin uso real hasta que **Aguado** lo defina.

## Estado del hardware (#13)

El protocolo declara dos canales EMG, pero el firmware y el gateway reales publican uno (`emg_lat_left`) hasta confirmar el hardware con Polfer. El formato de las tramas serie no cambia.

## Datos sin validar y dónde se cambian

Los datos que Aguado aún no ha validado (issue #106) tienen una fuente única por valor. Cambiar la fuente propaga el valor solo cuando la columna «Se actualiza a mano» está vacía; en el resto, la lista es cerrada y un test falla si una copia diverge. Todos siguen provisionales («PENDIENTE medición Aguado»): al cambiar un valor, mantén o actualiza la marca según corresponda.

## Dónde se cambia cada valor

| Valor (provisional) | Fuente única | Se actualiza a mano (lista cerrada) | Test que falla si diverge |
|---|---|---|---|
| Descanso entre series (30 s), series y repeticiones (4 × 15), banda (verde, 25 kg) | `protocol-catalog.json` | `example-mission.yaml` (`planned_sets`, `reps_per_set`, `rest_between_sets_s`, `load`); `app/ui/src/main/sessions/protocols.ts` (banda, series, reps; no lleva el descanso); mocks de `app/ui/src/renderer/data/mock/` | `contracts/tests/test_catalog.py` (cruce misión↔catálogo); `protocols.test.ts`; `protocol-values.test.ts` (mocks) |
| Descanso al terminar (`rest_seconds`, 120 s; provisional, pendiente de Aguado) | `protocol-catalog.json` | mocks de la UI; el texto «2 min» de la UI se deriva de este valor (`strings/session.ts`) | `protocol-values.test.ts`; `contracts/tests/test_catalog.py` (marca pendiente) |
| Ritmo (`rep_duration_s` 3,0 s, `rest_between_reps_s` 0,6 s), `gap_s` (30 s) y `rom_deg` (30°) | `protocol-catalog.json` | `example-mission.yaml` (`rep_duration_s`, `rest_between_reps_s`, `rom_deg`, `bracket.gap_s`) | `contracts/tests/test_catalog.py`; `physiology/tests/test_protocol_sets.py` |
| Longitud del cordón en reposo (650 mm; 666 mm del segundo astronauta) | `example-mission.yaml` (por astronauta, simulador) | `contracts/catalog/calibration-defaults.json` (de ahí lo lee el gateway; `SPINE_GATEWAY_STRETCH_REST_LENGTH_MM` lo cambia en el dispositivo real); `calibration.stretch.json`; `features-request.json`; READMEs de gateway, contracts y firmware | `engine/tests/test_features_stretch_simulator.py` lee la misión; `contracts/tests/test_calibration_defaults.py` cruza la misión con el fichero (solo AST-01, 650 mm) |
| Curva del cordón | `contracts/catalog/calibration-defaults.json` (curva de 4 puntos y reposo 6012 ohm / 650 mm); el gateway la escala a la R0 medida y `SPINE_GATEWAY_STRETCH_CURVE` la sustituye | `example-mission.yaml` (`signals.stretch.curve`) | `contracts/tests/test_calibration_defaults.py`; `gateway/tests/test_calibration.py`; `physiology/tests/test_signals_stretch.py` |
| Espera de recuperación del cordón (120 s) y fracción de recuperación (0,1) | `engine/src/spine_engine/settings.py` (valores por defecto del motor); `recovery_wait_s` de la calibración manda si llega | `example-mission.yaml` (`recovery_wait_s`), `calibration.stretch.json`, `features-request.json`, README del engine en/es/ca | `engine/tests/test_settings.py` (fija 120 y 0,1 y que `StretchFamily` usa el mismo valor) |
| Ventana MVC (250 ms) y `n_trials` (3) | `contracts/catalog/calibration-defaults.json` (`emg_mvc.window_ms`; `n_trials` solo en la misión) | `example-mission.yaml` (`emg_mvc`); `SPINE_GATEWAY_EMG_MVC_WINDOW_MS` (gateway); ejemplos `calibration.emg-mvc*.json`; READMEs del gateway | `contracts/tests/test_calibration_defaults.py`; `gateway/tests/test_settings.py` |
| Separación de electrodos, tramo, GND (10 cm, T11-L2, cresta ilíaca) e IMU (T9) | `session.exercise.json` / `example-mission.yaml` | `protocols.ts`; `strings/codes.ts` (texto) | `protocols.test.ts` (solo `streams`) |
| Textos de colocación y etiquetas de la UI | `app/ui/src/renderer/strings/` | todo a mano, y README/firmware en es/ca | ninguno |
| Pesos de rango, cortes de triaje | `engine/src/spine_engine/risk.py`, `triage-policy.json` | tablas del README del engine | `engine/tests/test_default_triage_policy.py` (marca «provisional») |

Qué se regenera: `protocol-catalog.json` no entra en los modelos Python ni en `v1.ts`; si cambia un schema, `uv run python contracts/scripts/generate_models.py` y `pnpm run gen:types`. Qué no cubre ningún test: los valores por defecto del gateway frente a la misión, y las notas en prosa de los README.
