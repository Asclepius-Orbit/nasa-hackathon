---
name: physiology-engine
description: Use when se cambia o añade un modelo, señal, feature, curva, fallo, escenario o scoring en `physiology` o `engine`, o cuando un cambio altera la salida simulada o el snapshot golden.
---

# physiology-engine

Guía para tocar el simulador (`spine_monitor/physiology`) y el motor (`spine_monitor/engine`). Todo cambio de código sigue `tdd-workflow`; esta skill solo añade lo específico del dominio. Para invariantes Hypothesis carga también `property-based-testing` (ver `.claude/skills/ROUTING.md`).

## 1. Determinismo: misma config + misma semilla = mismos bytes

- Todo el azar sale de `spine_physiology.rng` (`generator(seed, *keys)`, `RngTree`). Ramas por claves `(astronaut_id, day, slot, stream_id, purpose)`; cada rama es independiente, así que añadir una rama nueva no desplaza las demás. Una función estocástica recibe un `np.random.Generator` por parámetro (como `faults.py` y `device.py`), nunca lo crea por su cuenta.
- Prohibido en `physiology/src`: `np.random.*` global, `random`, `datetime.now()`, `time.time()`. Los únicos relojes de pared hoy son el gateway en vivo (`gateway/app.py`) y `sinks/pacing.py` (`clock` inyectable). El tiempo simulado sale de la config y del índice de día.
- Un UUID o `boot_id` nuevo se deriva con `rng.derive_uuid` / `derive_boot_id`, no con `uuid4()`.
- No dependas del orden de iteración de sets ni de la velocidad o del sink: `day_messages` y `session_messages` deben dar lo mismo que `iter_messages()` (README de `physiology`, sección Reproducibilidad).
- En `engine`, `created_at`/`computed_at` usan `datetime.now(UTC)` (`baseline/builder.py`, `stubs.py`): es metadato, no entra en cálculos ni en comparaciones de tests. Si necesitas reloj en lógica, inyéctalo.
- Valores numéricos clínicos o fisiológicos: nunca en el código. Salen del YAML de misión (`physiology/configs/example-mission.yaml`, números placeholder) y se validan en `config.py` (Pydantic, claves desconocidas rechazadas).

## 2. Unidades: pint en los bordes, floats en los bucles

- `pint` está en las dependencias de `physiology/pyproject.toml` pero **hoy no se usa en ningún `.py`**: las unidades viven en los nombres (`length_mm`, `rate_hz`, `_ohm`, `_s`, `_ms`). Antes de introducirlo, confirma con Uri dónde.
- Si lo usas: un `UnitRegistry` único por módulo, conversión en la lectura de config y en la escritura de payloads; dentro de la síntesis (arrays numpy, bucles por muestra) solo `float`/`ndarray` en una unidad base documentada. Nunca `Quantity` dentro de un bucle caliente ni dentro de arrays numpy.
- Los payloads de contrato llevan la unidad en el catálogo de features (`contracts/catalog`); no inventes otra.

## 3. Añadir o cambiar un modelo, feature, fallo o escenario

1. Lee el módulo vecino y copia su patrón (`signals/*.py`, `curves.py`, `faults.py`, `state.py`, `truth.py`).
2. Test rojo primero. Elige el marker (tabla de `tdd-workflow`): `unit` para el caso concreto, `property` para el invariante, `golden` solo para la salida completa.
3. Parámetros nuevos: obligatorios en el modelo Pydantic de `config.py` y en `example-mission.yaml`; sin valores por defecto en código.
4. **Feature nueva**: debe existir en el catálogo de `contracts/catalog`; la verdad de terreno (`truth.expected_features`) y la extracción del engine deben coincidir con el modelo sin ruido (patrón de `physiology/tests/test_truth.py`).
5. **Escenario nuevo**: hoy un escenario es una config YAML más `segments` (preflight 1 g, inflight microgravedad, postflight) y `gen.resimulate(ref, gravity)` para el par A/B. Reutiliza eso antes de crear otro mecanismo. `POST /simulate` del engine aplica `overrides` al último valor de cada feature (`simulation.py`); `project_days` aún no se aplica.
6. **Fallo nuevo** (`faults.py`): debe quedar registrado en la verdad de terreno (`quality_faults`, `lost_chunks`, `device_events`) y con su `QualityFlag` del contrato.
7. Si cambia un payload, un campo o un enum de contrato, para y carga `contract-first` antes de editar el modelo.

## 4. Invariantes a comprobar

Property tests de referencia: `physiology/tests/test_properties_curves.py`, `test_properties_rng.py`, `engine/tests/test_properties_scoring.py`. Estrategias sin `assume`/`filter` (construye la entrada válida, como `curves()`), floats acotados y sin NaN/inf salvo que pruebes justo eso.

- Rng: misma rama = mismo flujo; ramas distintas = flujos distintos (incluido entero vs texto `1`/`"1"`); UUID v4 válido; `boot_id` en 32 bits.
- Curvas: pasan por sus nudos, quedan dentro del rango de nudos, `values` vectorizado = llamada escalar.
- Scoring y detectores: z-score finito, la mediana puntúa 0, monótono en el valor; un pico aislado nunca es cambio sostenido.
- Límites fisiológicos y técnicos: latentes en `[0, 1]` salvo `spine_elongation` (mm); cuentas ADC dentro del rango; `seq`/`t0_device_ms` vuelven a 0 tras un reinicio; un mismo día regenerado por separado da los mismos payloads. Los límites son los de la config, no constantes del test.
- Cobertura de bordes que Hypothesis no encuentra solo: añade `@example` explícito (ver `test_different_branches_give_different_streams`).

## 5. Golden snapshots

- Existe uno: `physiology/tests/golden/tiny-mission.json`, generado por `physiology/tests/test_golden_mission.py` (misión mini, semilla fija, recuentos por tipo + sha256 de todos los mensajes, floats redondeados a 4 decimales).
- Úsalo solo para detectar cambios no intencionados de la salida completa. No lo uses para probar lógica: eso es `unit` o `property`.
- Si falla: primero averigua **por qué** cambió la salida. Cambio intencionado (modelo, rama rng, orden de mensajes, campo de contrato) y explicable en una frase: regenera. No explicable: es un bug de determinismo, arréglalo.
- Regenerar, desde `spine_monitor`: `UPDATE_GOLDEN=1 uv run pytest physiology/tests/test_golden_mission.py -q -p no:randomly`; vuelve a correr sin la variable; revisa el diff del JSON (cambian `messages`, `by_kind` o solo `sha256`) y explícalo en el mensaje de commit y en la PR. Commitéalo junto al cambio que lo causa.
- No añadas snapshots nuevos por comodidad; uno por comportamiento de extremo a extremo.

## 6. Comandos (desde `spine_monitor`)

- Bucle rápido: `uv run pytest physiology/tests/<fichero> -q -p no:randomly`, `uv run pytest engine -q`.
- Solo propiedades o golden: `uv run pytest physiology engine -m property` / `-m golden`.
- Gate completo (Docker, lo corre Uri o CI): ver `tdd-workflow`.

## Señales de alarma — PARA

| Pensamiento | Realidad |
|---|---|
| «Uso `np.random.normal` / `random` para esto, es solo una vez» | Rompe la reproducibilidad. Pide un `Generator` de `rng` por rama. |
| «Pongo `datetime.now()` en la señal» | El tiempo simulado sale de config y día. El reloj de pared solo vive en el gateway y en `pacing`. |
| «Regenero el golden para que pase» | Sin entender el diff, escondes un bug. Explica el cambio o arréglalo. |
| «Hardcodeo este umbral fisiológico, es razonable» | Todo número clínico sale del YAML y se valida en `config.py`. |
| «Envuelvo todo en `Quantity` de pint» | Pint en los bordes; dentro, floats/ndarrays en unidad base. |
| «Una rama rng nueva no afecta a nada» | Solo si la clave es nueva. Reusar o reordenar claves cambia los bytes. |
| «Cambio el payload y luego ajusto el contrato» | Contrato primero: skill `contract-first`. |
| «El property test pasa con `assume(...)`» | Construye entradas válidas; un `assume` que descarta casi todo no prueba nada. |
