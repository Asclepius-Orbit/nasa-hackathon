# Workflow TDD y puertas de calidad — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Todo cambio de código del workspace `spine_monitor` pasa por TDD y por un gate de calidad (lint, tipos, tests, cobertura, contratos, mutación) idéntico en local y en CI.

**Architecture:** La configuración vive en `spine_monitor/pyproject.toml`. Un script `quality/gate.sh` ejecuta el gate; una imagen Docker `quality` (con mosquitto) lo corre igual en local y en GitHub Actions. Una skill de proyecto `tdd-workflow` describe el proceso y `implement-feature` la invoca.

**Tech Stack:** Python 3.14, uv workspace, pytest, pytest-cov, pytest-randomly, pytest-timeout, Hypothesis, Schemathesis, ruff, pyright, mutmut o cosmic-ray (se decide en la Task 6), diff-cover, Docker Compose, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-02-tdd-workflow-design.md`

## Global Constraints

- Comandos desde `C:\code\AsclepiusOrbit\nasa-hackathon\spine_monitor` salvo que se indique otra cosa; shell Git Bash.
- Python `>=3.14.8,<3.15`. La imagen `python:3.14-slim` trae 3.14.10 y `.python-version` fija 3.14.9: los Dockerfiles NO copian `.python-version` (patrón de `engine/Dockerfile`).
- uv se instala en imágenes con `COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /bin/uv`. Nunca `pip install uv`.
- Gates bloqueantes: ruff sin errores; pyright sin errores; todos los tests verdes en orden aleatorio y con timeout; cobertura de líneas y ramas ≥ 90 %; diff coverage ≥ 90 % en PRs; mutación incremental ≥ 80 % en ficheros modificados en PRs; tests de contrato verdes. La mutación completa nocturna no bloquea.
- Dobles de test solo en fronteras (HTTP, MQTT, reloj, aleatoriedad); fakes antes que `unittest.mock`; nunca mockear lógica propia; integración con mosquitto real.
- Cada test lleva exactamente uno de los markers `unit`, `integration`, `contract`, `property`, `golden`; `mosquitto` es adicional.
- Sin líneas `Co-Authored-By` ni "generated with Claude" en commits ni PRs. Pedir confirmación a Uri antes de cada commit. Trabajar en la rama actual.
- En texto dirigido al equipo, llamar al usuario "Uri".
- READMEs: `README.md`, `README.es.md` y `README.ca.md` siempre sincronizados con enlace de cambio de idioma; nunca apuntar al vault de Obsidian.
- No se bajan umbrales por cuenta propia: si un número no se alcanza, se informa a Uri.

## Línea base medida (2026-11-02)

- `uv run --with pytest-cov pytest` sobre contracts, engine y physiology: 781 passed, 1 skipped (mosquitto), cobertura 96 % (4470 líneas, 888 ramas).
- Ruff con la config global del usuario (no está en el repo): 66 errores. Hay que fijar config en el repo.
- Type checkers sobre contracts, engine y physiology: mypy 237 errores, pyright 204, ty 198. Se elige pyright.
- `backend/src/monitor/{api,db,ingest,models}.py` están vacíos; `mcp_server` y `agent` solo tienen un `main()` de ejemplo.
- Ya existen tests de sincronía asyncapi↔topics (`contracts/tests/test_interface_docs.py`) y de deriva OpenAPI (`openapi_drift`); generator y gateway ya validan sus payloads contra los schemas. El trabajo nuevo de contratos es Schemathesis sobre el engine y etiquetar lo existente.
- La cobertura de fault injection del gateway ya es amplia (`test_hostile_payloads_never_bring_the_gateway_down`, `test_redelivered_commands_get_the_same_acks_without_re_executing`, `test_broker_drop_mid_run_reconnects_...`): los huecos nuevos se descubren con mutación (Task 11), no se inventan.

## Estructura de ficheros

| Fichero | Responsabilidad |
|---|---|
| `spine_monitor/pyproject.toml` (modificar) | testpaths, markers, coverage, ruff, pyright, dev deps |
| `spine_monitor/conftest.py` (crear) | asigna el marker por defecto de cada test |
| `spine_monitor/tests/test_markers.py` (crear) | tests del hook de markers |
| `spine_monitor/backend/tests/test_health.py`, `mcp_server/tests/test_main.py`, `agent/tests/test_main.py` (crear) | humo de los paquetes sin tests |
| `spine_monitor/quality/Dockerfile` (crear) | imagen con uv + dependencias de desarrollo |
| `spine_monitor/quality/gate.sh` (crear) | ejecuta el gate |
| `spine_monitor/quality/mutation.sh` (crear) | mutación incremental o completa |
| `app/docker-compose.yml` (modificar) | servicio `quality` |
| `.github/workflows/quality.yml`, `nightly-mutation.yml` (crear) | CI |
| `.claude/skills/tdd-workflow/SKILL.md` (crear) | proceso TDD |
| `.claude/skills/implement-feature/SKILL.md` (modificar) | integra la skill y el gate |
| `engine/tests/test_properties_scoring.py`, `physiology/tests/test_properties_curves.py`, `physiology/tests/test_properties_rng.py` (crear) | Hypothesis |
| `physiology/tests/test_golden_mission.py`, `physiology/tests/golden/tiny-mission.json` (crear) | golden |
| `engine/tests/test_contract_openapi_fuzz.py` (crear) | Schemathesis |

---

### Task 1: Configuración de pytest, markers, cobertura y herramientas

**Files:**
- Create: `spine_monitor/conftest.py`
- Create: `spine_monitor/tests/test_markers.py`
- Create: `spine_monitor/backend/tests/test_health.py`, `spine_monitor/mcp_server/tests/test_main.py`, `spine_monitor/agent/tests/test_main.py`
- Modify: `spine_monitor/pyproject.toml`

**Interfaces:**
- Produces: markers `unit|integration|contract|property|golden|mosquitto`; hook `pytest_collection_modifyitems` que añade `integration` a los tests con `mosquitto`, `contract` a los de `contracts/tests`, y `unit` al resto si no llevan ya uno de los cinco; `[tool.coverage]` con `fail_under = 90`.

- [ ] **Step 1: Escribir el test del hook (rojo)**

`spine_monitor/tests/test_markers.py`:

```python
from pathlib import Path

import pytest

KINDS = {"unit", "integration", "contract", "property", "golden"}
CONFTEST = Path(__file__).resolve().parents[1] / "conftest.py"


def test_every_test_ends_with_exactly_one_kind_marker(pytester):
    pytester.makeconftest(CONFTEST.read_text(encoding="utf-8"))
    pytester.makepyfile(
        test_plain="""
        def test_a(): pass
        """,
        test_explicit="""
        import pytest
        @pytest.mark.property
        def test_b(): pass
        """,
        test_broker="""
        import pytest
        @pytest.mark.mosquitto
        def test_c(): pass
        """,
    )
    items = pytester.inline_run("--collect-only", "-q").getcalls("pytest_itemcollected")
    kinds = {c.item.name: {m.name for m in c.item.iter_markers()} & KINDS for c in items}
    assert kinds == {"test_a": {"unit"}, "test_b": {"property"}, "test_c": {"integration"}}


@pytest.mark.parametrize("marker", sorted(KINDS | {"mosquitto"}))
def test_markers_are_registered(pytestconfig, marker):
    assert any(line.startswith(f"{marker}:") for line in pytestconfig.getini("markers"))
```

- [ ] **Step 2: Ejecutar y ver el fallo**

Run: `uv run pytest tests/test_markers.py -q -p no:randomly`
Expected: FAIL (no existe `conftest.py` y los markers no están registrados). Si el fallo es otro (p. ej. `FileNotFoundError` por el cwd de `pytester`), corregir el test hasta que falle por la razón correcta.

- [ ] **Step 3: Implementar `conftest.py`**

`spine_monitor/conftest.py`:

```python
"""Asigna a cada test exactamente un marker de tipo (unit, integration, contract, property, golden)."""

from pathlib import PurePath

import pytest

pytest_plugins = ["pytester"]  # lo necesita tests/test_markers.py; debe declararse en el conftest raíz

KINDS = ("unit", "integration", "contract", "property", "golden")


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if any(item.get_closest_marker(kind) for kind in KINDS):
            continue
        parts = PurePath(str(item.path)).parts
        if item.get_closest_marker("mosquitto"):
            kind = "integration"
        elif "contracts" in parts and "tests" in parts:
            kind = "contract"
        else:
            kind = "unit"
        item.add_marker(getattr(pytest.mark, kind))
```

- [ ] **Step 4: Configurar `pyproject.toml`**

Sustituir el bloque `[tool.pytest.ini_options]` y añadir el resto (mantener `[tool.uv.workspace]`):

```toml
[dependency-groups]
dev = [
    "pytest>=8",
    "pytest-cov",
    "pytest-randomly",
    "pytest-timeout",
    "hypothesis",
    "schemathesis",
    "diff-cover",
    "ruff",
    "pyright",
    "httpx",
]

[tool.pytest.ini_options]
testpaths = ["tests", "contracts/tests", "engine/tests", "physiology/tests", "backend/tests", "mcp_server/tests", "agent/tests"]
addopts = "--import-mode=importlib --strict-markers --timeout=120"
markers = [
    "unit: isolated logic, no I/O",
    "integration: several components together or a real broker",
    "contract: validates payloads or APIs against the JSON Schemas / OpenAPI / AsyncAPI",
    "property: Hypothesis property-based test",
    "golden: output compared with a versioned snapshot",
    "mosquitto: integration test against a real MQTT broker on 127.0.0.1:1883 (skipped when none is running)",
]

[tool.coverage.run]
branch = true
source = ["spine_contracts", "spine_engine", "spine_physiology", "monitor", "spine_mcp_server", "spine_agent"]

[tool.coverage.report]
fail_under = 90
show_missing = true
skip_empty = true

[tool.ruff]
target-version = "py314"
line-length = 160
extend-exclude = ["contracts/src/spine_contracts/models"]

[tool.ruff.lint]
select = ["E", "F", "I", "B", "UP"]

[tool.pyright]
pythonVersion = "3.14"
include = ["contracts/src", "engine/src", "physiology/src", "backend/src", "mcp_server/src", "agent/src"]
exclude = ["contracts/src/spine_contracts/models"]
typeCheckingMode = "standard"
```

Run: `uv sync --all-packages` (resuelve y fija las dependencias de desarrollo en `uv.lock`).
Expected: termina sin error y `uv.lock` cambia.

- [ ] **Step 5: Tests de humo de los paquetes sin tests (rojo, luego verde)**

`backend/tests/test_health.py`:

```python
from fastapi.testclient import TestClient
from monitor.main import app


def test_health_reports_ok():
    assert TestClient(app).get("/health").json() == {"status": "ok"}
```

`mcp_server/tests/test_main.py`:

```python
from spine_mcp_server import main


def test_main_prints_its_greeting(capsys):
    main()
    assert capsys.readouterr().out == "Hello from spine-mcp-server!\n"
```

`agent/tests/test_main.py`:

```python
from spine_agent import main


def test_main_prints_its_greeting(capsys):
    main()
    assert capsys.readouterr().out == "Hello from spine-agent!\n"
```

Run: `uv run pytest tests backend/tests mcp_server/tests agent/tests -q`
Expected: PASS (el código ya existe; estos tests documentan su comportamiento actual y dan al gate algo que medir).

- [ ] **Step 6: Suite completa con marker por defecto**

Run: `uv run pytest -q`
Expected: 781+ passed, 1 skipped, sin `PytestUnknownMarkWarning`.

Run: `uv run pytest -m "not unit and not integration and not contract and not property and not golden" --collect-only -q`
Expected: `no tests collected` (ningún test sin tipo).

- [ ] **Step 7: Cobertura medida con la config**

Run: `uv run pytest -q --cov --cov-report=term | tail -5`
Expected: línea `TOTAL` ≥ 90 % y `Required test coverage of 90% reached`. Si no llega, parar e informar a Uri con el número.

- [ ] **Step 8: Commit (pedir confirmación a Uri)**

```bash
git add spine_monitor/pyproject.toml spine_monitor/uv.lock spine_monitor/conftest.py spine_monitor/tests spine_monitor/backend/tests spine_monitor/mcp_server/tests spine_monitor/agent/tests
git commit -m "Add test markers, coverage threshold, tooling config and smoke tests for every package"
```

---

### Task 2: Ruff verde

**Files:**
- Modify: ficheros que señale ruff.

**Interfaces:**
- Consumes: `[tool.ruff]` de la Task 1.
- Produces: `uv run ruff check .` sin errores.

- [ ] **Step 1: Medir**

Run: `uv run ruff check . --statistics`
Expected: lista de reglas con recuentos (la config del repo sustituye a la global del usuario).

- [ ] **Step 2: Aplicar arreglos seguros**

Run: `uv run ruff check . --fix && uv run pytest -q`
Expected: suite verde tras los arreglos automáticos.

- [ ] **Step 3: Arreglar a mano lo restante**

Run: `uv run ruff check .`
Para cada error restante: si es un error real (p. ej. nombre no definido), escribir primero un test que lo exponga (rojo), arreglar, ver verde. Si es de estilo, arreglar directamente.
Expected al final: `All checks passed!` y `uv run pytest -q` verde.

- [ ] **Step 4: Commit (pedir confirmación a Uri)**

```bash
git add -A spine_monitor
git commit -m "Fix ruff findings under the repository lint configuration"
```

---

### Task 3: Pyright verde (con subagentes)

**Files:**
- Modify: ficheros de `contracts/src`, `engine/src`, `physiology/src` que señale pyright (excepto `contracts/src/spine_contracts/models`, generado).

**Interfaces:**
- Consumes: `[tool.pyright]` de la Task 1.
- Produces: `uv run pyright` con `0 errors`.

- [ ] **Step 1: Medir y repartir**

Run: `uv run pyright --outputjson > /tmp/pyright.json; uv run pyright 2>&1 | tail -3`
Agrupar los errores por paquete (`contracts`, `engine`, `physiology`, el resto). Línea base previa: 204 errores sobre los tres paquetes, muchos de numpy/scipy (`Array` incompatible con `_SupportsArray`).

- [ ] **Step 2: Un subagente por paquete, en paralelo (un solo mensaje)**

Modelo `sonnet`, esfuerzo medio. Prompt de cada uno: «Corrige todos los errores de pyright de `<paquete>/src` ejecutando `uv run pyright <paquete>/src` desde `spine_monitor`. Reglas: (1) no cambiar comportamiento; la suite del paquete debe seguir verde (`uv run pytest <paquete>/tests -q`); (2) si un error revela un bug real, escribe primero un test que falle, muéstralo en rojo, arregla y muéstralo en verde, y repórtalo; (3) prohibido `# type: ignore` y `Any` para silenciar sin motivo; si un ignore es inevitable (stub de numpy incorrecto), usa `# pyright: ignore[regla]` con un comentario de una línea con el motivo; (4) no toques otros paquetes. Devuelve: errores antes/después, lista de ficheros tocados, bugs reales encontrados.»

- [ ] **Step 3: Revisar el diff real de cada subagente**

Run: `git diff --stat && git diff -- '*.py' | head -400`
Comprobar que no hay `# type: ignore` sueltos ni cambios de comportamiento no justificados. Cada bug real tiene su test.

- [ ] **Step 4: Verificar**

Run: `uv run pyright && uv run ruff check . && uv run pytest -q`
Expected: `0 errors`, ruff limpio, suite verde.

- [ ] **Step 5: Commit (pedir confirmación a Uri; un commit por paquete si el diff es grande)**

```bash
git add -A spine_monitor
git commit -m "Fix pyright findings in contracts, engine and physiology"
```

---

### Task 4: Skill `tdd-workflow`

**Files:**
- Create: `.claude/skills/tdd-workflow/SKILL.md`

**Interfaces:**
- Produces: skill invocable como `tdd-workflow`; cita el comando `docker compose -f app/docker-compose.yml run --rm quality` (se crea en la Task 5) y el atajo local `cd spine_monitor && uv run pytest`.

- [ ] **Step 1: Escribir la skill**

````markdown
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

## Dobles de test

- Solo en las fronteras: HTTP, MQTT, reloj, aleatoriedad.
- Fakes escritos a mano o inyección de dependencias antes que `unittest.mock`. Modelo: `FakeClient` y `Harness` de `physiology/tests/test_gateway.py`.
- Nunca mockees lógica propia.
- La integración MQTT usa mosquitto real.

## Gate (todo bloqueante)

Comando único, igual en local y CI:

```bash
docker compose -f app/docker-compose.yml run --rm quality
```

Ejecuta, en orden: `ruff check`, `pyright`, `pytest` con cobertura (líneas y ramas ≥ 90 %, orden aleatorio, timeout), y mutación incremental (≥ 80 % en ficheros modificados). En PRs, CI añade diff coverage ≥ 90 %. Un nightly corre la mutación completa sin bloquear.

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
````

- [ ] **Step 2: Verificar que la skill se lista**

Run: `ls .claude/skills/tdd-workflow/SKILL.md && head -4 .claude/skills/tdd-workflow/SKILL.md`
Expected: el fichero existe y el frontmatter tiene `name` y `description`. (Probar en una sesión nueva que `tdd-workflow` aparece en la lista de skills.)

- [ ] **Step 3: Commit (pedir confirmación a Uri)**

```bash
git add .claude/skills/tdd-workflow
git commit -m "Add tdd-workflow skill"
```

---

### Task 5: Imagen Docker `quality`, `gate.sh` y servicio en compose

**Files:**
- Create: `spine_monitor/quality/Dockerfile`
- Create: `spine_monitor/quality/gate.sh`
- Modify: `app/docker-compose.yml`

**Interfaces:**
- Produces: `docker compose -f app/docker-compose.yml run --rm quality` ejecuta el gate completo contra mosquitto real (el contenedor comparte la red de mosquitto, así `127.0.0.1:1883` funciona sin cambiar los tests). Variable `GATE_MUTATION` (`off` por defecto en esta task; la Task 6 le da valor).

- [ ] **Step 1: Dockerfile**

`spine_monitor/quality/Dockerfile` (contexto: raíz del repo):

```dockerfile
# syntax=docker/dockerfile:1
# Quality gate image. Build context: repo root.
#   docker build -f spine_monitor/quality/Dockerfile -t spine-quality .
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /bin/uv

ENV UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

# The workspace lives at the same path the compose file bind-mounts, so editable installs resolve.
WORKDIR /work/spine_monitor

COPY spine_monitor/pyproject.toml spine_monitor/uv.lock ./
COPY spine_monitor/contracts/pyproject.toml contracts/
COPY spine_monitor/physiology/pyproject.toml physiology/
COPY spine_monitor/engine/pyproject.toml engine/
COPY spine_monitor/mcp_server/pyproject.toml mcp_server/
COPY spine_monitor/agent/pyproject.toml agent/
COPY spine_monitor/backend/pyproject.toml backend/

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --all-packages --no-install-workspace

COPY spine_monitor /work/spine_monitor
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --all-packages

CMD ["sh", "quality/gate.sh"]
```

- [ ] **Step 2: Script del gate**

`spine_monitor/quality/gate.sh`:

```sh
#!/bin/sh
# Quality gate: every step is blocking. Same command locally and in CI.
set -eu

echo "==> ruff"
ruff check .

echo "==> pyright"
pyright

echo "==> tests + coverage (random order, timeout, real mosquitto)"
pytest -q --cov --cov-report=term-missing --cov-report=xml:coverage.xml

if [ "${GATE_MUTATION:-off}" != "off" ]; then
    echo "==> mutation (${GATE_MUTATION})"
    sh quality/mutation.sh "${GATE_MUTATION}"
fi

echo "==> gate passed"
```

- [ ] **Step 3: Servicio en compose**

Añadir a `app/docker-compose.yml`, bajo `services:`:

```yaml
  quality:
    build:
      context: ..
      dockerfile: spine_monitor/quality/Dockerfile
    image: spine-quality:dev
    # Shares mosquitto's network namespace so the tests' 127.0.0.1:1883 reaches the broker.
    network_mode: "service:mosquitto"
    depends_on:
      mosquitto:
        condition: service_healthy
    volumes:
      - ../spine_monitor:/work/spine_monitor
    environment:
      GATE_MUTATION: "${GATE_MUTATION:-off}"
    profiles: ["quality"]
```

- [ ] **Step 4: Construir y ejecutar**

Run: `docker compose -f app/docker-compose.yml --profile quality build quality`
Expected: build correcto.

Run: `docker compose -f app/docker-compose.yml run --rm quality`
Expected: `==> gate passed`, y en la salida de pytest `1 skipped` pasa a `0 skipped` (el test `mosquitto` ahora corre contra el broker real). Si `127.0.0.1:1883` del host ya está ocupado por otro mosquitto, el servicio `mosquitto` no arranca: parar el otro y reintentar.

- [ ] **Step 5: Comprobar que el gate falla de verdad**

Introducir un error de lint (`import os` sin usar en un fichero de `engine/src`), ejecutar el comando anterior y ver `ruff` fallar; revertir.
Expected: el contenedor sale con código distinto de 0 en el paso `ruff`.

- [ ] **Step 6: Commit (pedir confirmación a Uri)**

```bash
git add spine_monitor/quality app/docker-compose.yml
git commit -m "Add the quality gate image, script and compose service with a real mosquitto"
```

---

### Task 6: Mutación (spike acotado y decisión mutmut/cosmic-ray)

**Files:**
- Create: `spine_monitor/quality/mutation.sh`
- Modify: `spine_monitor/pyproject.toml` (config de la herramienta elegida)

**Interfaces:**
- Consumes: imagen `quality` de la Task 5.
- Produces: `sh quality/mutation.sh changed|full`, que sale con código ≠ 0 si el mutation score de los ficheros modificados (`changed`) es < 80 %; `full` imprime el score por paquete y siempre sale con 0.

**Contexto del spike:** mutmut 3 corre en Docker con Python 3.14 y genera los mutantes, pero con el layout `src/` del workspace dice «could not find any test case for any mutant»: los tests importan el paquete instalado en editable, no la copia de `mutants/`. Probado sin éxito: `also_copy = ["engine/tests","contracts","pyproject.toml"]` y `PYTHONPATH=/tmp/sm/mutants/engine/src`.

- [ ] **Step 1: Spike mutmut con límite de 45 minutos**

Dentro de la imagen `quality` (`docker compose -f app/docker-compose.yml run --rm --entrypoint sh quality`), sobre `engine/src/spine_engine/deviation/scoring.py` (13 mutantes, 63 tests). Probar, en este orden, hasta que `mutmut results` muestre mutantes `killed` o `survived` (no `not checked`):
1. Instalar los miembros no editables: `uv sync --frozen --all-packages --no-editable` y relanzar.
2. Quitar el paquete instalado del import (`uv pip uninstall spine-engine`) y poner `mutants/engine/src` y `mutants/contracts/src` en `PYTHONPATH`.
3. Activar `debug = true` en `[tool.mutmut]` y leer qué tests se ejecutan.
Si pasados 45 minutos no hay resultado, ir al Step 2. Si funciona, escribir `[tool.mutmut]` definitivo en `pyproject.toml` (`source_paths`, `also_copy`, `pytest_add_cli_args_test_selection`) y saltar al Step 4 con mutmut.

- [ ] **Step 2: Spike cosmic-ray**

`uv add --dev cosmic-ray`. Config de prueba `quality/cosmic-ray.toml`:

```toml
[cosmic-ray]
module-path = "engine/src/spine_engine/deviation/scoring.py"
timeout = 30.0
excluded-modules = []
test-command = "pytest -x -q -p no:randomly engine/tests/test_scoring.py"

[cosmic-ray.distributor]
name = "local"
```

Run: `cosmic-ray init quality/cosmic-ray.toml /tmp/session.sqlite && cosmic-ray exec quality/cosmic-ray.toml /tmp/session.sqlite && cr-report /tmp/session.sqlite`
Expected: informe con mutantes matados y supervivientes. Si funciona, esta es la herramienta; documentar la config final en `quality/cosmic-ray.toml`.

- [ ] **Step 3: Si ninguna funciona**

Parar e informar a Uri con la salida exacta de ambas; decidir con él (p. ej. reestructurar la instalación). No inventar una tercera vía sin consultar.

- [ ] **Step 4: Escribir `mutation.sh` (con la herramienta que funcionó)**

El script recibe `changed` o `full`. En `changed`, calcula los ficheros `*.py` de `*/src/**` modificados respecto a `origin/develop` (`git diff --name-only origin/develop...HEAD -- '*/src/*.py'`), ejecuta la mutación solo sobre ellos y compara el score con 80 %; sin ficheros modificados, sale con 0. En `full`, recorre todos los paquetes con código y sale con 0. Escribir primero el caso `changed` con un fichero cuyo score se conozca (el spike de `scoring.py`), verlo salir 0, y luego forzar un fallo borrando una aserción de `engine/tests/test_scoring.py` y ver que sale ≠ 0; restaurar.

- [ ] **Step 5: Medir la línea base**

Run: `GATE_MUTATION=full docker compose -f app/docker-compose.yml run --rm quality`
Expected: score por paquete. **Parar y dar a Uri los números**: si algún paquete queda por debajo de 80 %, se decide con él (la Task 11 mata supervivientes), no se baja el umbral.

- [ ] **Step 6: Commit (pedir confirmación a Uri)**

```bash
git add spine_monitor/quality spine_monitor/pyproject.toml spine_monitor/uv.lock
git commit -m "Add incremental and full mutation testing to the quality gate"
```

---

### Task 7: GitHub Actions

**Files:**
- Create: `.github/workflows/quality.yml`
- Create: `.github/workflows/nightly-mutation.yml`

**Interfaces:**
- Consumes: servicio `quality` y `GATE_MUTATION`.

- [ ] **Step 1: Workflow de PR**

`.github/workflows/quality.yml`:

```yaml
name: quality

on:
  pull_request:
    branches: [develop]

jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Quality gate (lint, types, tests, coverage, incremental mutation)
        env:
          GATE_MUTATION: changed
        run: docker compose -f app/docker-compose.yml run --rm quality
      - name: Diff coverage
        run: |
          pip install diff-cover
          diff-cover spine_monitor/coverage.xml --compare-branch=origin/develop --fail-under=90
```

- [ ] **Step 2: Workflow nocturno**

`.github/workflows/nightly-mutation.yml`:

```yaml
name: nightly-mutation

on:
  schedule:
    - cron: "0 3 * * *"
  workflow_dispatch:

jobs:
  mutation:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Full mutation report (non blocking)
        env:
          GATE_MUTATION: full
        run: docker compose -f app/docker-compose.yml run --rm quality
```

- [ ] **Step 3: Validar sintaxis**

Run: `uv run --with pyyaml python -c "import yaml,sys; [yaml.safe_load(open(f)) for f in ('.github/workflows/quality.yml','.github/workflows/nightly-mutation.yml')]; print('ok')"` (desde la raíz del repo)
Expected: `ok`. La ejecución real solo se comprueba al abrir el PR; avisar a Uri de que la primera ejecución en GitHub es la verificación definitiva.

- [ ] **Step 4: Commit (pedir confirmación a Uri)**

```bash
git add .github
git commit -m "Add the pull request quality workflow and the nightly mutation report"
```

---

### Task 8: Tests property-based (Hypothesis)

**Files:**
- Create: `spine_monitor/engine/tests/test_properties_scoring.py`
- Create: `spine_monitor/physiology/tests/test_properties_curves.py`
- Create: `spine_monitor/physiology/tests/test_properties_rng.py`

**Interfaces:**
- Consumes: `RobustZScorer.score(value: float, entry: BaselineEntry) -> float` (`entry` solo se lee por `median`, `mad`, `std`); `PiecewiseLinear(points)`, `.values(xs)`, `.__call__(x)`; `dose_drift(dose_ratios, drift_per_day)`; `rng.generator(seed, *keys)`, `rng.derive_uuid(seed, *keys)`.

- [ ] **Step 1: Scoring (rojo, luego verde)**

`engine/tests/test_properties_scoring.py`:

```python
import math
from types import SimpleNamespace
from typing import cast

import pytest
from hypothesis import given, strategies as st
from spine_contracts import BaselineEntry
from spine_engine.deviation.scoring import RobustZScorer

pytestmark = pytest.mark.property

finite = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)
spread = st.floats(min_value=0.0, max_value=1e6, allow_nan=False, allow_infinity=False)


def entry(median: float, mad: float, std: float) -> BaselineEntry:
    return cast(BaselineEntry, SimpleNamespace(median=median, mad=mad, std=std))


@given(value=finite, median=finite, mad=spread, std=spread)
def test_score_is_always_finite(value, median, mad, std):
    assert math.isfinite(RobustZScorer().score(value, entry(median, mad, std)))


@given(median=finite, mad=spread, std=spread)
def test_the_median_scores_zero(median, mad, std):
    assert RobustZScorer().score(median, entry(median, mad, std)) == 0.0


@given(a=finite, b=finite, median=finite, mad=spread, std=spread)
def test_score_is_monotonic_in_the_value(a, b, median, mad, std):
    scorer, e = RobustZScorer(), entry(median, mad, std)
    low, high = sorted((a, b))
    assert scorer.score(low, e) <= scorer.score(high, e)


@given(value=finite, median=finite, mad=st.floats(min_value=1e-3, max_value=1e6), std=spread)
def test_score_uses_the_mad_when_it_is_not_degenerate(value, median, mad, std):
    expected = (value - median) / (1.4826 * mad)
    assert RobustZScorer().score(value, entry(median, mad, std)) == pytest.approx(expected)
```

Run: `uv run pytest engine/tests/test_properties_scoring.py -q -p no:randomly`
Expected: PASS. Para cada propiedad, comprobar que **puede** fallar: cambiar temporalmente `MAD_TO_SIGMA` a `1.0` en `scoring.py` y ver rojo en `test_score_uses_the_mad...`; restaurar. (Es la comprobación de «rojo» de un test que cubre código ya existente.)

- [ ] **Step 2: Curvas**

`physiology/tests/test_properties_curves.py`:

```python
import numpy as np
import pytest
from hypothesis import assume, given, strategies as st
from spine_physiology.curves import PiecewiseLinear, dose_drift

pytestmark = pytest.mark.property

coord = st.floats(min_value=-1e3, max_value=1e3, allow_nan=False, allow_infinity=False)


@st.composite
def curves(draw):
    xs = sorted(draw(st.sets(st.floats(min_value=-1e3, max_value=1e3, allow_nan=False, allow_infinity=False), min_size=1, max_size=8)))
    assume(all(b - a > 1e-6 for a, b in zip(xs, xs[1:])))
    ys = [draw(coord) for _ in xs]
    return list(zip(xs, ys))


@given(points=curves())
def test_curve_passes_through_its_knots(points):
    curve = PiecewiseLinear(points)
    for x, y in points:
        assert curve(x) == pytest.approx(y, abs=1e-9)


@given(points=curves(), x=coord)
def test_curve_stays_within_the_knot_range(points, x):
    ys = [y for _, y in points]
    assert min(ys) - 1e-9 <= PiecewiseLinear(points)(x) <= max(ys) + 1e-9


@given(points=curves(), xs=st.lists(coord, min_size=1, max_size=20))
def test_vectorised_values_match_the_scalar_call(points, xs):
    curve = PiecewiseLinear(points)
    assert np.allclose(curve.values(np.array(xs)), [curve(x) for x in xs])


@given(drift=coord, n=st.integers(min_value=1, max_value=30))
def test_dose_drift_is_zero_when_the_plan_is_met(drift, n):
    assert np.allclose(dose_drift([1.0] * n, drift), 0.0)


@given(ratios=st.lists(st.floats(min_value=0.0, max_value=2.0), min_size=1, max_size=30), drift=coord)
def test_dose_drift_is_the_running_sum_of_the_daily_shortfall(ratios, drift):
    expected = np.cumsum([drift * (1.0 - r) for r in ratios])
    assert np.allclose(dose_drift(ratios, drift), expected)
```

Run: `uv run pytest physiology/tests/test_properties_curves.py -q -p no:randomly`
Expected: PASS. Comprobación de rojo: cambiar `1.0 - ratios` por `ratios - 1.0` en `dose_drift` y ver fallar la última propiedad; restaurar.

- [ ] **Step 3: Rng**

`physiology/tests/test_properties_rng.py`:

```python
import re

import pytest
from hypothesis import given, strategies as st
from spine_physiology import rng

pytestmark = pytest.mark.property

seeds = st.integers(min_value=0, max_value=2**63)
keys = st.lists(st.one_of(st.text(max_size=12), st.integers(min_value=-1000, max_value=1000)), max_size=5)
UUID_V4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


@given(seed=seeds, ks=keys)
def test_a_branch_is_reproducible(seed, ks):
    assert rng.generator(seed, *ks).random(5).tolist() == rng.generator(seed, *ks).random(5).tolist()


@given(seed=seeds, a=keys, b=keys)
def test_different_branches_give_different_streams(seed, a, b):
    if a == b:
        return
    assert rng.generator(seed, *a).random(8).tolist() != rng.generator(seed, *b).random(8).tolist()


@given(seed=seeds, ks=keys)
def test_derived_uuids_are_valid_v4_and_stable(seed, ks):
    value = rng.derive_uuid(seed, *ks)
    assert UUID_V4.fullmatch(value)
    assert value == rng.derive_uuid(seed, *ks)


@given(seed=seeds, ks=keys)
def test_boot_id_fits_32_bits(seed, ks):
    assert 0 <= rng.derive_boot_id(seed, *ks) < 2**32
```

Run: `uv run pytest physiology/tests/test_properties_rng.py -q -p no:randomly`
Expected: PASS. Si `test_different_branches...` falla por colisión entre claves `1` y `True`-like o `"1"` e `1`, es un hallazgo real: `_key_word` etiqueta `i:`/`s:`, así que no debería; si ocurre, reportar a Uri.

- [ ] **Step 4: Suite y cobertura**

Run: `uv run pytest -q --cov | tail -4`
Expected: verde y cobertura ≥ 90 %.

- [ ] **Step 5: Commit (pedir confirmación a Uri)**

```bash
git add spine_monitor/engine/tests/test_properties_scoring.py spine_monitor/physiology/tests/test_properties_curves.py spine_monitor/physiology/tests/test_properties_rng.py
git commit -m "Add Hypothesis property tests for scoring, curves and the rng tree"
```

---

### Task 9: Test golden de physiology

**Files:**
- Create: `spine_monitor/physiology/tests/test_golden_mission.py`
- Create: `spine_monitor/physiology/tests/golden/tiny-mission.json`

**Interfaces:**
- Consumes: `MissionGenerator(config, seed=None)`, `.iter_messages() -> Iterator[Envelope]`; `Envelope(kind, topic, payload, at, retained, astronaut_id, day)`; `MissionConfig.model_validate(raw)`; `physiology/configs/example-mission.yaml`.

- [ ] **Step 1: Escribir el test (rojo: no existe el snapshot)**

`physiology/tests/test_golden_mission.py`:

```python
"""La misión mini con semilla fija reproduce un snapshot versionado (recuentos por tipo + hash de los mensajes)."""

import hashlib
import json
import os
from collections import Counter
from pathlib import Path

import pytest
import yaml
from spine_physiology import MissionGenerator
from spine_physiology.config import MissionConfig

pytestmark = pytest.mark.golden

EXAMPLE = Path(__file__).resolve().parents[1] / "configs" / "example-mission.yaml"
GOLDEN = Path(__file__).parent / "golden" / "tiny-mission.json"
SEED = 20261102


def tiny_config() -> MissionConfig:
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    raw["start_mission_day"] = -1
    raw["segments"] = [
        {"days": 1, "phase": "preflight", "gravity_environment": "earth_1g"},
        {"days": 2, "phase": "inflight", "gravity_environment": "microgravity"},
        {"days": 1, "phase": "postflight", "gravity_environment": "earth_1g"},
    ]
    raw["device"].update(power_on_time_utc="09:00", power_off_time_utc="09:40")
    raw["protocols"]["lat_pulldown_standing_v1"].update(lead_in_s=2.0, planned_reps=3, rep_duration_s=2.0, rest_between_reps_s=1.0, lead_out_s=6.0)
    raw["protocols"]["spine_length_rest_v1"]["duration_s"] = 5.0
    times = {"am_spine_check": "09:05", "lat_pulldown": "09:20", "pm_spine_check": "09:35"}
    for s in raw["schedule"]["sessions"]:
        s.update(time_utc=times[s["name"]], start_jitter_s=20.0)
    raw["schedule"]["background"].update(start_time_utc="09:19", end_time_utc="09:22")
    raw["signals"]["chunk_ms"] = 500
    return MissionConfig.model_validate(raw)


def _rounded(value, digits=4):
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, dict):
        return {k: _rounded(v, digits) for k, v in value.items()}
    if isinstance(value, list):
        return [_rounded(v, digits) for v in value]
    return value


def snapshot() -> dict:
    digest = hashlib.sha256()
    counts: Counter[str] = Counter()
    total = 0
    for env in MissionGenerator(tiny_config(), seed=SEED).iter_messages():
        counts[env.kind.value] += 1
        total += 1
        line = json.dumps([env.kind.value, env.topic, env.at.isoformat(), env.retained, _rounded(env.payload)], sort_keys=True, separators=(",", ":"))
        digest.update(line.encode())
    return {"seed": SEED, "messages": total, "by_kind": dict(sorted(counts.items())), "sha256": digest.hexdigest()}


def test_tiny_mission_matches_the_golden_snapshot():
    current = snapshot()
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    assert GOLDEN.exists(), "golden snapshot missing: run with UPDATE_GOLDEN=1 and review the diff"
    assert current == json.loads(GOLDEN.read_text(encoding="utf-8"))
```

Run: `uv run pytest physiology/tests/test_golden_mission.py -q -p no:randomly`
Expected: FAIL con `golden snapshot missing`.

- [ ] **Step 2: Generar el snapshot y revisarlo**

Run: `UPDATE_GOLDEN=1 uv run pytest physiology/tests/test_golden_mission.py -q -p no:randomly && cat physiology/tests/golden/tiny-mission.json`
Expected: PASS y un JSON con `messages` > 0, `by_kind` con `stream_chunk`, `device_status`, `session`, etc. Comprobar que los recuentos son razonables.

- [ ] **Step 3: Verde sin actualizar, y estable entre ejecuciones**

Run: `uv run pytest physiology/tests/test_golden_mission.py -q -p no:randomly` dos veces.
Expected: PASS ambas.

- [ ] **Step 4: Comprobar que detecta una regresión**

Cambiar temporalmente `sigma` (o cualquier constante de ruido) en `physiology/src/spine_physiology/signals/emg.py`, ejecutar el test y ver fallar por `sha256`; restaurar.
Expected: FAIL con el hash distinto, y PASS tras restaurar.

- [ ] **Step 5: Estable entre plataformas**

Run: `docker compose -f app/docker-compose.yml run --rm quality pytest physiology/tests/test_golden_mission.py -q`
Expected: PASS en Linux con el snapshot generado en Windows. Si falla (diferencias de punto flotante entre plataformas), reducir `_rounded(..., digits=4)` a 3 y regenerar; si persiste, parar e informar a Uri antes de decidir (p. ej. generar el snapshot solo en Docker).

- [ ] **Step 6: Commit (pedir confirmación a Uri)**

```bash
git add spine_monitor/physiology/tests/test_golden_mission.py spine_monitor/physiology/tests/golden
git commit -m "Add a golden snapshot test for the seeded tiny mission"
```

---

### Task 10: Schemathesis sobre el engine

**Files:**
- Create: `spine_monitor/engine/tests/test_contract_openapi_fuzz.py`

**Interfaces:**
- Consumes: `create_default_app()` de `spine_engine.api`; contrato `spine_monitor/contracts/openapi.yaml`. Nota: los handlers del engine leen el cuerpo a mano, así que el OpenAPI generado por FastAPI no lleva schemas de petición; el oráculo es el YAML del contrato.

- [ ] **Step 1: Comprobar la API de la versión instalada**

Run: `uv run python -c "import schemathesis as s; print(s.__version__); print([n for n in dir(s.openapi) if not n.startswith('_')])"`
Expected: versión y funciones del namespace `openapi` (en la serie 4.x: `from_path`, `from_dict`, `from_asgi`, `from_url`...). Usar los nombres que imprima; si difieren de los del Step 2, adaptar el test.

- [ ] **Step 2: Escribir el test (rojo si la API del engine incumple el contrato)**

```python
"""Fuzz de la API real del engine contra el contrato OpenAPI (contracts/openapi.yaml)."""

from pathlib import Path

import pytest
import schemathesis
from fastapi.testclient import TestClient
from spine_engine.api import create_default_app

pytestmark = pytest.mark.contract

CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"
schema = schemathesis.openapi.from_path(str(CONTRACT))


@schema.parametrize()
def test_engine_answers_what_the_contract_promises(case):
    client = TestClient(create_default_app())
    response = case.call(session=client, base_url="http://testserver")
    case.validate_response(response)
```

Run: `uv run pytest engine/tests/test_contract_openapi_fuzz.py -q -p no:randomly`
Expected: o PASS, o fallos reales de contrato (p. ej. un 500 con cuerpo inválido, un `Content-Type` distinto del contrato). Cada fallo es un hallazgo: escribir un test de regresión concreto en `engine/tests/test_api.py` que lo reproduzca (rojo), arreglar el engine (verde), y repetir hasta que el fuzz pase. Si `case.call(session=...)` no admite un `TestClient`, usar el mecanismo ASGI/WSGI de la versión instalada (Step 1) en vez de inventar uno.

- [ ] **Step 3: Verificar que el fuzz detecta una desviación**

Cambiar temporalmente el `Content-Type` de errores de `application/problem+json` a `application/json` en `engine/src/spine_engine/api/problems.py`, ejecutar y ver fallar; restaurar.
Expected: FAIL y luego PASS.

- [ ] **Step 4: Alcance honesto**

backend y app aún no tienen API implementada (`backend/src/monitor/api.py` está vacío; `app-openapi.yaml` lo implementa otra issue). Dejar en la skill/README una línea: «añadir el mismo test de Schemathesis al implementar esas APIs».

- [ ] **Step 5: Commit (pedir confirmación a Uri)**

```bash
git add spine_monitor/engine
git commit -m "Fuzz the engine API against its OpenAPI contract with Schemathesis"
```

---

### Task 11: Matar supervivientes de mutación (guiado por datos)

**Files:**
- Modify: tests de `contracts/tests`, `engine/tests`, `physiology/tests` según el informe.

**Interfaces:**
- Consumes: `GATE_MUTATION=full` de la Task 6.

Esta tarea no puede llevar el código de los tests por adelantado: los supervivientes salen del informe. El procedimiento es fijo y cada ciclo cumple el TDD.

- [ ] **Step 1: Informe**

Run: `GATE_MUTATION=full docker compose -f app/docker-compose.yml run --rm quality`
Expected: lista de mutantes supervivientes por fichero. Ordenar por paquete y empezar por los de menor score.

- [ ] **Step 2: Por cada superviviente, un ciclo**

1. Leer el mutante (la línea y el cambio, p. ej. `<` → `<=`).
2. Escribir un test que pase con el código original y falle con el mutante. Ejemplo para `scoring.py`: si sobrevive el cambio de `_ABSOLUTE_FLOOR = 1e-6` a `1e-5`, añadir en `engine/tests/test_scoring.py`:

```python
def test_a_scale_just_above_the_floor_is_used_as_is():
    entry = SimpleNamespace(median=0.0, mad=0.0, std=5e-6)
    assert RobustZScorer().score(1.0, entry) == pytest.approx(1.0 / 5e-6)
```

3. Ver el test pasar con el original y fallar con el mutante (`mutmut`/`cosmic-ray` lo confirman al re-ejecutar ese fichero).
4. Si el mutante es equivalente (no cambia el comportamiento observable), documentarlo en una línea en el informe a Uri; no añadir exclusiones sin su visto bueno.

- [ ] **Step 3: Paralelizar por paquete**

Lanzar subagentes `sonnet` (esfuerzo medio), uno por paquete (`contracts`, `engine`, `physiology`), con este prompt: «Mata los mutantes supervivientes de `<paquete>` siguiendo el ciclo del Step 2 de la Task 11 del plan. Un test por superviviente o grupo coherente, en el fichero de tests existente del módulo. Devuelve el score antes/después y los mutantes equivalentes.» Revisar el diff real de cada uno.

- [ ] **Step 4: Verificar el umbral**

Run: `GATE_MUTATION=full docker compose -f app/docker-compose.yml run --rm quality`
Expected: score ≥ 80 % por paquete. Si un paquete no llega tras razonar los equivalentes, informar a Uri con los números; no bajar el umbral.

- [ ] **Step 5: Commit por paquete (pedir confirmación a Uri)**

```bash
git add spine_monitor/<paquete>/tests
git commit -m "Kill surviving mutants in <paquete>"
```

---

### Task 12: Integrar `implement-feature` con `tdd-workflow`

**Files:**
- Modify: `.claude/skills/implement-feature/SKILL.md`

**Interfaces:**
- Consumes: skill `tdd-workflow` (Task 4), servicio `quality` (Task 5).

- [ ] **Step 1: Fase 4**

En la sección «Fase 4 — Implementación», sustituir la frase «Usa las skills/agentes identificados en Fase 1 (p. ej. `superpowers:test-driven-development`, `superpowers:subagent-driven-development`).» por:

```
Usa las skills/agentes identificados en Fase 1 (p. ej. `superpowers:subagent-driven-development`). **Toda implementación y bugfix de código sigue `tdd-workflow`**: cada subagente recibe su ciclo (rojo visible, verde, refactor) y debe devolver la salida del test en rojo y en verde, no solo el resumen. Sin rojo visible, la parte se rechaza.
```

- [ ] **Step 2: Fase 5**

En «Fase 5 — Verificación», sustituir el primer punto («Ejecuta tests, lint y build del repo; si no hay, ejecuta la feature realmente (skill `run` si aplica).») por:

```
- Ejecuta el gate completo: `docker compose -f app/docker-compose.yml run --rm quality` (ruff, pyright, tests con cobertura ≥ 90 %, mutación incremental ≥ 80 %). Si Docker no está disponible, para y díselo a Uri; no lo sustituyas por una ejecución parcial sin decirlo. Para código fuera de `spine_monitor` (build/tests propios), ejecuta además los comandos que descubras en el repo.
- Comprueba que cada parte tiene su test visto en rojo y que ningún umbral se ha bajado.
```

Y añadir a la tabla «Señales de alarma — PARA» la fila:

```
| "Los tests pasan en local, no corro el gate" | El gate de Docker es el criterio; local no basta. |
```

- [ ] **Step 3: Comprobar coherencia**

Run: `grep -n "tdd-workflow\|run --rm quality" .claude/skills/implement-feature/SKILL.md`
Expected: aparecen en Fase 4, Fase 5 y la tabla.

- [ ] **Step 4: Commit (pedir confirmación a Uri)**

```bash
git add .claude/skills/implement-feature/SKILL.md
git commit -m "Make implement-feature use tdd-workflow and the quality gate"
```

---

### Task 13: Documentación y vault

**Files:**
- Modify: `spine_monitor/README.md`, `spine_monitor/README.es.md`, `spine_monitor/README.ca.md`

- [ ] **Step 1: Sección de calidad en los tres READMEs, sincronizados**

Añadir a cada uno una sección (en su idioma; conservar el enlace de cambio de idioma existente) con: el comando del gate, el bucle rápido `uv run pytest <ruta> -q -p no:randomly`, los umbrales (cobertura ≥ 90 %, mutación ≥ 80 % en ficheros modificados), los markers, y el aviso de que en CI el diff coverage también exige ≥ 90 %. Sin referencias al vault.

Texto en español (`README.es.md`); traducir fielmente a `README.md` (inglés) y `README.ca.md` (catalán):

```markdown
## Calidad y TDD

Todo cambio de código sigue TDD (ver la skill `tdd-workflow`) y pasa el gate de calidad:

    docker compose -f app/docker-compose.yml run --rm quality

Ejecuta ruff, pyright, los tests con cobertura (líneas y ramas ≥ 90 %, orden aleatorio, mosquitto real) y la mutación incremental (≥ 80 % en los ficheros modificados). En los PR, CI exige además diff coverage ≥ 90 %; un nightly genera el informe de mutación completo.

Bucle rápido: `cd spine_monitor && uv run pytest <ruta> -q -p no:randomly`.

Cada test lleva un marker: `unit`, `integration`, `contract`, `property` o `golden` (`mosquitto` es adicional).
```

- [ ] **Step 2: Vault (leer antes, escribir después)**

Con las herramientas MCP del vault: `search_vault` por «testing» en `projects/Asclepius Orbit` para no duplicar; `log_decision` con la decisión (TDD con rojo visible; gate en Docker; umbrales; pyright; herramienta de mutación elegida y por qué; excepciones) y `create_note` con la nota de estrategia de testing en `projects/Asclepius Orbit/`. Si se actualiza una nota existente, `update_note` **sobrescribe toda la nota**: leerla y reenviar el contenido completo. Pedir confirmación a Uri antes de escribir.

- [ ] **Step 3: Verificación final**

Run: `docker compose -f app/docker-compose.yml run --rm quality`
Expected: `==> gate passed`.

Run: `git status --short && git log --oneline -15`
Expected: árbol limpio salvo lo que Uri decida; commits sin líneas `Co-Authored-By`.

- [ ] **Step 4: Commit de docs (pedir confirmación a Uri)**

```bash
git add spine_monitor/README.md spine_monitor/README.es.md spine_monitor/README.ca.md
git commit -m "Document the TDD workflow and the quality gate in the READMEs"
```

---

### Task 14: Mutación más rápida en re-ejecuciones (spike acotado, pedido por Uri)

**Files:**
- Modify: `spine_monitor/quality/mutation.sh`, `app/docker-compose.yml`, `.github/workflows/quality.yml`, `.github/workflows/nightly-mutation.yml` (solo lo que la medición justifique)

**Interfaces:**
- Consumes: `sh quality/mutation.sh changed|full` y `MUTATION_STAGE`, `MUTATION_PACKAGES`, `MUTATION_BASE` de la Task 6.
- Produces: tiempos medidos antes y después; opcionalmente `MUTATION_JOBS` (procesos hijos de mutmut), un volumen Docker con nombre para el directorio de trabajo de mutmut, y caché de CI.

**Contexto medido:** la corrida completa de physiology tarda 108 min en un contenedor inactivo; `mutation.sh` prepara su copia en `/tmp/mutation` y la descarta con `--rm`, así que cada corrida empieza de cero. Lanzar unos 5 contenedores de mutación en paralelo hace que la fase de tests limpios agote el timeout y aborte la corrida. Restricciones: no recortar mutantes ni código, no bajar el umbral del 80 %, el mutation score de cada fichero no debe cambiar por la optimización.

- [ ] **Step 1: Línea base de tiempos.** En un contenedor inactivo, con `nproc` anotado, medir `sh quality/mutation.sh changed physiology/src/spine_physiology/signals/stretch.py` (385 mutantes) y `full` del paquete `engine`. Registrar segundos, mutantes y mutantes por segundo.
- [ ] **Step 2: Paralelismo.** Leer cómo `mutation.sh` invoca `mutmut run` y qué admite mutmut 3.8.1 (`mutmut run --help`, procesos hijos). Probar valores crecientes sobre el mismo fichero; registrar tiempo y confirmar que el score no cambia y que la fase de tests limpios no hace timeout.
- [ ] **Step 3: Estado persistente.** Montar un volumen Docker con nombre en `MUTATION_STAGE`. Ejecutar el mismo fichero tres veces: sin cambios, cambiando el cuerpo de una función de `src`, y cambiando solo un test. Registrar cuánto tarda cada una y si mutmut reutiliza los resultados de los mutantes ya evaluados. Si no reutiliza, decirlo con la evidencia y no seguir con la caché.
- [ ] **Step 4: Perfil de physiology.** Medir mutantes por segundo por módulo de `physiology/src` para saber qué módulos dominan y proponer grupos para una matriz de CI.
- [ ] **Step 5: Implementar solo lo que se haya medido como útil.** `MUTATION_JOBS` en `mutation.sh` con un valor por defecto seguro; volumen con nombre en el servicio `quality` si el Step 3 lo justifica; `actions/cache` (acción fijada a una versión mayor) con clave por hash de `src` y `tests` de cada paquete, y matriz por paquete en el nightly, si los Steps 3 y 4 lo justifican.
- [ ] **Step 6: Verificación.** Repetir las mediciones del Step 1 con los cambios y mostrar la mejora con números. Comprobar que el score por fichero es el mismo y que la prueba de pasa/falla de `changed` sigue funcionando (un test debilitado hace salir ≠ 0). Validar el YAML.
- [ ] **Step 7: Commit (pedir confirmación a Uri)**

```bash
git add spine_monitor/quality app/docker-compose.yml .github
git commit -m "Speed up repeated mutation runs with parallel mutmut jobs and a persistent state"
```

**Resultado (2026-11-04, medido, no adoptado):** no hay commit de código. mutmut 3.8.1 ya usa por defecto todos los núcleos (`max_children` = 22): con 8, 11 y 33 procesos `stretch.py` (385 mutantes) tarda 438, 466 y 449 s frente a 367-381 s por defecto. Dos corridas idénticas cambian el veredicto de unos 3 mutantes (92,5 % frente a 91,7 %). Una caché de `mutants/` en un volumen reutiliza resultados (re-ejecución sin cambios 136 s, un cambio de función 186 s), pero mutmut no invalida el estado al cambiar solo los tests: con todos los tests de `stretch` desactivados la caché seguía dando 92,5 % cuando el resultado real era 83,1 %, así que dejaría pasar `changed` con tests debilitados. Opción segura no construida ni medida: `actions/cache` con clave del hash exacto de src, tests, `pyproject.toml` y `uv.lock`, sin restore-keys (solo acelera re-ejecuciones idénticas). El tiempo de la corrida completa solo baja repartiendo physiology por módulos en una matriz de CI (physiology son unas 6051 de unas 7480 líneas de código fuente); ese reparto no está medido.

---

### Tasks 15 a 17: decisiones de Uri tras la revisión final (2026-11-04)

- **Task 17 — Contrato 422.** Documentar en `contracts/openapi.yaml` que `POST /baseline` y `POST /assess` pueden devolver 422 `insufficient_data` con una petición válida, y quitar la exclusión del check ST202 del fuzz de Schemathesis (`engine/tests/test_contract_openapi_fuzz.py`). Uri avisa al equipo del cambio de contrato.
- **Task 15 — Mutación solo de las funciones tocadas.** Motivo: los tiempos de mutación están medidos en 22 CPUs y un runner de GitHub para repo privado tiene 2 vCPU; `changed` muta ficheros enteros y el primer PR de esta rama (10 ficheros de physiology) excedería el timeout de 120 min. `changed` debe mutar solo las funciones del diff (con mutmut 3.8.1, que identifica mutantes por función), con medición antes y después, sin bajar el umbral del 80 % ni recortar mutantes. El nightly completo en un runner de 2 vCPU sigue sin resolverse.
- **Task 16 — Mutantes de argumentos de log.** Excluir por configuración de mutmut, sin `pragma: no mutate` en el código, solo si existe un mecanismo limpio y medido; si no, informar y no hacer nada.
- Decisión sobre protección de rama (I1): aceptar sin protección; el check de CI informa pero no bloquea.

**Resultados (2026-11-04):**
- **Task 17:** solo `POST /baseline` puede devolver 422 `insufficient_data` (único `raise` en `baseline/builder.py`); se documentó en `openapi.yaml` sin subir versión. La exclusión del check ST202 del fuzz se mantiene porque Schemathesis 4.33 no lee las respuestas documentadas en ese check.
- **Task 15:** `changed` muta solo las funciones y métodos tocados por el diff. Un diff de una función (`resistance_to_adc`): 34 mutantes en 171 s frente a 385 mutantes y ~395 s de todo `stretch.py`. Todo el diff de esta rama: 1541 mutantes en lugar de 7840, 796-880 s y 92 % en 22 CPUs; en un runner de 2 vCPU se estima (extrapolado, no medido) en unas 2 h, al límite del timeout de 120 min. Se corrigieron falsos "matados" del paquete engine (pytest no puede seleccionar por id los tests de Schemathesis y salía con 4, que mutmut cuenta como matado): el score real de engine es 83,4 % (546/655), no el 94,8 % que daba. No se añadió regla de muestra mínima: un mutante que cambia de veredicto mueve la puntuación 100/N puntos.
- **Task 16:** `do_not_mutate_patterns` de mutmut 3.8.1 salta los argumentos de las llamadas de log (`log`/`_log`/`logger`/`_logger`/`logging` más `debug|info|warning|warn|error|exception|critical|log`) sin `pragma` en el código: 125 de los 8993 mutantes de physiology (todos de `gateway/app.py`), no ~150. Verificado comparando las listas completas antes y después. Excluye también 7 mutantes de log que hoy se matan.
- **Decisiones posteriores de Uri (Task 18):** el job del PR sube a 240 min; el workflow de mutación completa pasa a manual (`workflow_dispatch`, con entrada opcional `packages`) porque una corrida completa de physiology (~108 min en 22 CPUs) no cabe en un runner de 2 vCPU ni en el máximo de 360 min, y se ejecuta en local con `GATE_MUTATION=full`; `changed` imprime la puntuación pero no falla cuando la selección tiene menos de 10 mutantes (`MUTATION_MIN_SAMPLE`, 0 la desactiva), regla consciente que deja pasar cambios pequeños sin que sus tests maten los mutantes; `MUTATION_MIN_SAMPLE` y `MUTATION_THRESHOLD` se validan al principio (salida 2 si no son numéricos). Sin protección de rama en el repo (plan gratuito): el check informa pero no bloquea.

---

## Autorrevisión frente al spec

| Requisito del spec | Task |
|---|---|
| Skill `tdd-workflow` | 4 |
| Integración con `implement-feature` | 12 |
| Config en pyproject (testpaths, markers, coverage, ruff, tipos) | 1, 2, 3 |
| Puertas bloqueantes | 5 (gate), 6 (mutación), 7 (diff coverage) |
| Docker `quality` con mosquitto | 5 |
| GitHub Actions (PR y nightly) | 7 |
| Contratos: Schemathesis | 10 |
| Contratos: payloads MQTT/AsyncAPI y sincronía spec↔modelos | ya existen (línea base); se etiquetan `contract` por `conftest.py` en la Task 1 |
| Property-based | 8 |
| Golden | 9 |
| Fault injection | cubierto en gran parte por tests existentes; huecos nuevos vía mutación en la Task 11 |
| Verificaciones previas (mutación, tipos, Schemathesis) | 3 (pyright elegido), 6 (spike), 10 (Step 1) |
| Línea base y umbrales sin bajarlos | 1 (Step 7), 6 (Step 5), 11 |
| Documentación y vault | 13 |

**Desviaciones del spec, por decisión de Uri o por evidencia:**
- Type checker: pyright, y los ~200 errores se corrigen ahora con subagentes (decisión de Uri).
- Schemathesis solo sobre el engine hasta que backend y app tengan API.
- Fault injection: no se inventan tests nuevos; la mutación indica los huecos reales.
