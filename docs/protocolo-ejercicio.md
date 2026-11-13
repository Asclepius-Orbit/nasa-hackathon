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
