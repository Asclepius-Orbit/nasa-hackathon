---
name: tdd-workflow
description: Use when se va a escribir o cambiar código de producción (feature, bugfix, refactor) en este repo, antes de escribir la primera línea de código.
---

# tdd-workflow

Todo cambio de código sigue TDD y pasa el gate de calidad. **Violar la letra del ciclo es violar su espíritu.**

## Ciclo (obligatorio)

1. **Rojo.** Escribe un test que describa el comportamiento. Ejecútalo y **muestra el fallo**; comprueba que falla por la razón correcta (assertion o import esperado, no un error de sintaxis o de fixture). Sin rojo visible no hay código.
2. **Verde.** Escribe el cambio mínimo. Ejecuta y **muestra el verde**.
3. **Refactor.** Solo con la suite en verde; vuelve a ejecutar tras cada paso.
4. **Bugfix.** Primero un test de regresión que reproduce el fallo, visto en rojo; luego el arreglo.

## Exentos (solo estos)

- Docs y README.
- Config/infra sin lógica (Dockerfile, compose, pyproject, CI): se verifica ejecutándola.
- Spikes etiquetados como throwaway; no se mergean tal cual.

Cualquier otro cambio de código sigue el ciclo.

## Qué test escribir

| Marker | Cuándo |
|---|---|
| `unit` | lógica aislada, sin I/O |
| `integration` | varios componentes juntos o broker real (`mosquitto`) |
| `contract` | payloads/APIs contra JSON Schema, OpenAPI, AsyncAPI |
| `property` | invariantes con Hypothesis (scoring, curvas, rng) |
| `golden` | salida determinista comparada con snapshot versionado |

Cada test lleva exactamente un marker de esta tabla; `conftest.py` pone `unit` por defecto, `integration` a los `mosquitto` y `contract` a los de `contracts/tests`. `mosquitto` es adicional.

El engine ya se fuzzea con Schemathesis contra `contracts/openapi.yaml` (`engine/tests/test_contract_openapi_fuzz.py`); al implementar las APIs de backend y app, añadir el mismo test de Schemathesis.

## Skills de apoyo

Según lo que toque el cambio, carga también (tabla completa en `.claude/skills/ROUTING.md`): `property-based-testing` para invariantes y ida y vuelta (marker `property`), `python-testing-patterns` para fixtures y parametrización, `async-python-patterns` si hay asyncio, `python-type-safety` si pyright protesta. Este fichero manda sobre ellas en markers, dobles y umbrales.

## Dobles de test

- Solo en las fronteras: HTTP, MQTT, reloj, aleatoriedad.
- Fakes escritos a mano o inyección de dependencias antes que `unittest.mock`. Modelo: `FakeClient` y `Harness` de `physiology/tests/test_gateway.py`.
- Nunca mockees lógica propia.
- La integración MQTT usa mosquitto real.

## Gate (todo bloqueante)

Comando único, igual en local y CI:

```bash
GATE_MUTATION=changed docker compose -f app/docker-compose.yml run --rm quality
```

Ejecuta, en orden: `ruff check`, `pyright`, `pytest` con cobertura (líneas y ramas ≥ 90 %, orden aleatorio, timeout), y mutación incremental (≥ 80 % en las funciones y métodos modificados). Sin `GATE_MUTATION=changed` se salta la mutación (bucle rápido). `changed` compara contra `origin/develop` por defecto (`MUTATION_BASE` lo cambia) y solo cuenta cambios commiteados: commitea antes de correrlo. Si `changed` puntúa menos de 10 mutantes (`MUTATION_MIN_SAMPLE`, 0 lo desactiva) y queda bajo 80 %, muestra la puntuación pero no falla (decisión de Uri: con tan pocos mutantes la puntuación es ruido). En PRs, CI añade diff coverage ≥ 90 %. La mutación completa se corre a mano: en local con `GATE_MUTATION=full` (hay también un workflow manual), no en un nightly.

Bucle rápido mientras desarrollas: `cd spine_monitor && uv run pytest <ruta> -q -p no:randomly`.

## Mutación: qué hacer con un superviviente

Un mutante vivo es un test que falta. Escribe el test que lo mata (rojo con el mutante, verde sin él). No añadas `# pragma: no mutate` para esquivarlo sin consultar a Uri.

## Señales de alarma — PARA

| Pensamiento | Realidad |
|---|---|
| «Escribo el código y luego los tests» | Sin rojo visible no hay TDD. Borra y empieza por el test. |
| «Es un cambio trivial» | Trivial sigue el ciclo; solo los exentos de arriba se saltan. |
| «El test pasa a la primera» | Un test que nunca falló no prueba nada. Rómpelo a propósito. |
| «Mockeo esta función mía para aislar» | Solo fronteras. Mockear lógica propia oculta bugs. |
| «La cobertura es 90 %, suficiente» | Cobertura mide ejecución, no verificación. Mira la mutación. |
| «Bajo el umbral solo esta vez» | No se bajan umbrales sin Uri. |
| «Pongo `# type: ignore` y sigo» | Solo `# pyright: ignore[regla]` con motivo. |
