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

Los datos que Aguado aún no ha validado (issue #106) se cambian en un solo lugar de configuración, no en el código. Un test cruza la misión del simulador con el catálogo, así que olvidar una copia falla en CI.

| Dato | Valor provisional | Dónde se cambia |
|---|---|---|
| Descanso entre series | 30 s | `rest_between_sets_s` en `protocol-catalog.json` (backend y UI); `rest_between_sets_s` de `lat_pulldown_standing_v1` en `example-mission.yaml`; copia en `app/ui/src/main/sessions/protocols.ts` si es necesario |
| Series, repeticiones, banda | 4 × 15, verde, 25 kg | `protocol-catalog.json`; `example-mission.yaml` (`planned_sets`, `reps_per_set`, `load`); `protocols.ts` |
| Ritmo («moderado») | 3,0 s por repetición y 0,6 s entre repeticiones en el simulador | `rep_duration_s`, `rest_between_reps_s` en `example-mission.yaml` |
| Longitud del cordón en reposo | 650 mm | `SPINE_GATEWAY_STRETCH_REST_LENGTH_MM` (gateway); calibración de `example-mission.yaml` y `calibration.stretch.json` |
| Curva del cordón | factores 1,0 / 1,25 / 1,5 / 1,75 / 2,0 sobre R0 y L0 | `SPINE_GATEWAY_STRETCH_CURVE_FACTORS` (gateway, lista JSON); curva de `example-mission.yaml` |
| Espera de recuperación del cordón | 120 s | `recovery_wait_s` en `example-mission.yaml` y `calibration.stretch.json` |
| Separación de electrodos, tramo, GND | 10 cm, T11-L2, cresta ilíaca | `placement` de `session.exercise.json` y de `emgLat` en `protocols.ts`; `example-mission.yaml` |
| MVC | sin procedimiento | `emg_mvc` de `example-mission.yaml` (`window_ms`, `n_trials`); `SPINE_GATEWAY_EMG_MVC_WINDOW_MS` (gateway) |
| Textos de colocación y etiquetas de la UI | provisionales | `app/ui/src/renderer/strings/` |
