# TDD workflow and quality gates — Implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every code change in the `spine_monitor` workspace goes through TDD and a quality gate (lint, types, tests, coverage, contracts, mutation) identical locally and in CI.

**Architecture:** The configuration lives in `spine_monitor/pyproject.toml`. A `quality/gate.sh` script runs the gate; a `quality` Docker image (with mosquitto) runs it the same way locally and in GitHub Actions. A project skill `tdd-workflow` describes the process and `implement-feature` invokes it.

**Tech Stack:** Python 3.14, uv workspace, pytest, pytest-cov, pytest-randomly, pytest-timeout, Hypothesis, Schemathesis, ruff, pyright, mutmut or cosmic-ray (decided in Task 6), diff-cover, Docker Compose, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-11-02-tdd-workflow-design.md`

## Global Constraints

- Commands run from `C:\code\AsclepiusOrbit\nasa-hackathon\spine_monitor` unless stated otherwise; Git Bash shell.
- Python `>=3.14.8,<3.15`. The `python:3.14-slim` image ships 3.14.10 and `.python-version` pins 3.14.9: the Dockerfiles do NOT copy `.python-version` (pattern from `engine/Dockerfile`).
- uv is installed in images with `COPY --from=ghcr.io/astral-sh/uv:0.12.7 /uv /bin/uv`. Never `pip install uv`.
- Blocking gates: ruff with no errors; pyright with no errors; all tests green in random order and with a timeout; line and branch coverage ≥ 90 %; diff coverage ≥ 90 % on PRs; incremental mutation ≥ 80 % on modified files on PRs; contract tests green. The full nightly mutation run does not block.
- Test doubles only at the boundaries (HTTP, MQTT, clock, randomness); fakes before `unittest.mock`; never mock our own logic; integration with a real mosquitto.
- Each test carries exactly one of the markers `unit`, `integration`, `contract`, `property`, `golden`; `mosquitto` is additional.
- No `Co-Authored-By` lines or "generated with Claude" in commits or PRs. Ask the maintainer for confirmation before each commit. Work on the current branch.
- In text addressed to the team, refer to the user as "the maintainer".
- READMEs: `README.md`, `README.es.md` and `README.ca.md` always in sync with a language-switch link; never point to the Obsidian vault.
- Thresholds are not lowered unilaterally: if a number is not reached, report it to the maintainer.

## Measured baseline (2026-11-02)

- `uv run --with pytest-cov pytest` over contracts, engine and physiology: 781 passed, 1 skipped (mosquitto), coverage 96 % (4470 lines, 888 branches).
- Ruff with the user's global config (not in the repo): 66 errors. A config must be pinned in the repo.
- Type checkers over contracts, engine and physiology: mypy 237 errors, pyright 204, ty 198. Pyright is chosen.
- `backend/src/monitor/{api,db,ingest,models}.py` are empty; `mcp_server` and `agent` only have an example `main()`.
- Tests for asyncapi↔topics sync (`contracts/tests/test_interface_docs.py`) and OpenAPI drift (`openapi_drift`) already exist; generator and gateway already validate their payloads against the schemas. The new contract work is Schemathesis over the engine and labelling what exists.
- The gateway's fault-injection coverage is already broad (`test_hostile_payloads_never_bring_the_gateway_down`, `test_redelivered_commands_get_the_same_acks_without_re_executing`, `test_broker_drop_mid_run_reconnects_...`): new gaps are discovered through mutation (Task 11), not invented.

## File structure

| File | Responsibility |
|---|---|
| `spine_monitor/pyproject.toml` (modify) | testpaths, markers, coverage, ruff, pyright, dev deps |
| `spine_monitor/conftest.py` (create) | assigns the default marker to each test |
| `spine_monitor/tests/test_markers.py` (create) | tests of the marker hook |
| `spine_monitor/backend/tests/test_health.py`, `mcp_server/tests/test_main.py`, `agent/tests/test_main.py` (create) | smoke tests for the packages without tests |
| `spine_monitor/quality/Dockerfile` (create) | image with uv + development dependencies |
| `spine_monitor/quality/gate.sh` (create) | runs the gate |
| `spine_monitor/quality/mutation.sh` (create) | incremental or full mutation |
| `app/docker-compose.yml` (modify) | `quality` service |
| `.github/workflows/quality.yml`, `nightly-mutation.yml` (create) | CI |
| `.claude/skills/tdd-workflow/SKILL.md` (create) | TDD process |
| `.claude/skills/implement-feature/SKILL.md` (modify) | integrates the skill and the gate |
| `engine/tests/test_properties_scoring.py`, `physiology/tests/test_properties_curves.py`, `physiology/tests/test_properties_rng.py` (create) | Hypothesis |
| `physiology/tests/test_golden_mission.py`, `physiology/tests/golden/tiny-mission.json` (create) | golden |
| `engine/tests/test_contract_openapi_fuzz.py` (create) | Schemathesis |

---

### Task 1: pytest configuration, markers, coverage and tooling

**Files:**
- Create: `spine_monitor/conftest.py`
- Create: `spine_monitor/tests/test_markers.py`
- Create: `spine_monitor/backend/tests/test_health.py`, `spine_monitor/mcp_server/tests/test_main.py`, `spine_monitor/agent/tests/test_main.py`
- Modify: `spine_monitor/pyproject.toml`

**Interfaces:**
- Produces: markers `unit|integration|contract|property|golden|mosquitto`; a `pytest_collection_modifyitems` hook that adds `integration` to tests with `mosquitto`, `contract` to those in `contracts/tests`, and `unit` to the rest if they do not already carry one of the five; `[tool.coverage]` with `fail_under = 90`.

- [ ] **Step 1: Write the hook test (red)**

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

- [ ] **Step 2: Run and see the failure**

Run: `uv run pytest tests/test_markers.py -q -p no:randomly`
Expected: FAIL (`conftest.py` does not exist and the markers are not registered). If the failure is something else (e.g. `FileNotFoundError` from `pytester`'s cwd), fix the test until it fails for the right reason.

- [ ] **Step 3: Implement `conftest.py`**

`spine_monitor/conftest.py`:

```python
"""Assigns each test exactly one kind marker (unit, integration, contract, property, golden)."""

from pathlib import PurePath

import pytest

pytest_plugins = ["pytester"]  # needed by tests/test_markers.py; must be declared in the root conftest

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

- [ ] **Step 4: Configure `pyproject.toml`**

Replace the `[tool.pytest.ini_options]` block and add the rest (keep `[tool.uv.workspace]`):

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

Run: `uv sync --all-packages` (resolves and pins the development dependencies in `uv.lock`).
Expected: finishes without error and `uv.lock` changes.

- [ ] **Step 5: Smoke tests for the packages without tests (red, then green)**

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
Expected: PASS (the code already exists; these tests document its current behaviour and give the gate something to measure).

- [ ] **Step 6: Full suite with the default marker**

Run: `uv run pytest -q`
Expected: 781+ passed, 1 skipped, no `PytestUnknownMarkWarning`.

Run: `uv run pytest -m "not unit and not integration and not contract and not property and not golden" --collect-only -q`
Expected: `no tests collected` (no untyped test).

- [ ] **Step 7: Coverage measured with the config**

Run: `uv run pytest -q --cov --cov-report=term | tail -5`
Expected: `TOTAL` line ≥ 90 % and `Required test coverage of 90% reached`. If it does not get there, stop and report the number to the maintainer.

- [ ] **Step 8: Commit (ask the maintainer for confirmation)**

```bash
git add spine_monitor/pyproject.toml spine_monitor/uv.lock spine_monitor/conftest.py spine_monitor/tests spine_monitor/backend/tests spine_monitor/mcp_server/tests spine_monitor/agent/tests
git commit -m "Add test markers, coverage threshold, tooling config and smoke tests for every package"
```

---

### Task 2: Green ruff

**Files:**
- Modify: files that ruff flags.

**Interfaces:**
- Consumes: `[tool.ruff]` from Task 1.
- Produces: `uv run ruff check .` with no errors.

- [ ] **Step 1: Measure**

Run: `uv run ruff check . --statistics`
Expected: list of rules with counts (the repo config replaces the user's global one).

- [ ] **Step 2: Apply safe fixes**

Run: `uv run ruff check . --fix && uv run pytest -q`
Expected: suite green after the automatic fixes.

- [ ] **Step 3: Fix the rest by hand**

Run: `uv run ruff check .`
For each remaining error: if it is a real error (e.g. undefined name), first write a test that exposes it (red), fix, see green. If it is style, fix directly.
Expected at the end: `All checks passed!` and `uv run pytest -q` green.

- [ ] **Step 4: Commit (ask the maintainer for confirmation)**

```bash
git add -A spine_monitor
git commit -m "Fix ruff findings under the repository lint configuration"
```

---

### Task 3: Green pyright (with subagents)

**Files:**
- Modify: files in `contracts/src`, `engine/src`, `physiology/src` that pyright flags (except `contracts/src/spine_contracts/models`, generated).

**Interfaces:**
- Consumes: `[tool.pyright]` from Task 1.
- Produces: `uv run pyright` with `0 errors`.

- [ ] **Step 1: Measure and split**

Run: `uv run pyright --outputjson > /tmp/pyright.json; uv run pyright 2>&1 | tail -3`
Group the errors by package (`contracts`, `engine`, `physiology`, the rest). Previous baseline: 204 errors over the three packages, many from numpy/scipy (`Array` incompatible with `_SupportsArray`).

- [ ] **Step 2: One subagent per package, in parallel (a single message)**

Model `sonnet`, medium effort. Prompt for each: "Fix all pyright errors in `<package>/src` by running `uv run pyright <package>/src` from `spine_monitor`. Rules: (1) do not change behaviour; the package's suite must stay green (`uv run pytest <package>/tests -q`); (2) if an error reveals a real bug, first write a failing test, show it red, fix and show it green, and report it; (3) `# type: ignore` and `Any` to silence without a reason are forbidden; if an ignore is unavoidable (incorrect numpy stub), use `# pyright: ignore[rule]` with a one-line comment giving the reason; (4) do not touch other packages. Return: errors before/after, list of files touched, real bugs found."

- [ ] **Step 3: Review each subagent's real diff**

Run: `git diff --stat && git diff -- '*.py' | head -400`
Check that there are no stray `# type: ignore` and no unjustified behaviour changes. Every real bug has its test.

- [ ] **Step 4: Verify**

Run: `uv run pyright && uv run ruff check . && uv run pytest -q`
Expected: `0 errors`, ruff clean, suite green.

- [ ] **Step 5: Commit (ask the maintainer for confirmation; one commit per package if the diff is large)**

```bash
git add -A spine_monitor
git commit -m "Fix pyright findings in contracts, engine and physiology"
```

---

### Task 4: `tdd-workflow` skill

**Files:**
- Create: `.claude/skills/tdd-workflow/SKILL.md`

**Interfaces:**
- Produces: a skill invocable as `tdd-workflow`; it cites the command `docker compose -f app/docker-compose.yml run --rm quality` (created in Task 5) and the local shortcut `cd spine_monitor && uv run pytest`.

- [ ] **Step 1: Write the skill**

````markdown
---
name: tdd-workflow
description: Use when production code is about to be written or changed (feature, bugfix, refactor) in this repo, before writing the first line of code.
---

# tdd-workflow

Every code change follows TDD and passes the quality gate. **Violating the letter of the cycle is violating its spirit.**

## Cycle (mandatory)

1. **Red.** Write a test that describes the behaviour. Run it and **show the failure**; check that it fails for the right reason (an assertion or an expected import, not a syntax or fixture error). No visible red, no code.
2. **Green.** Write the minimal change. Run it and **show the green**.
3. **Refactor.** Only with the suite green; re-run after each step.
4. **Bugfix.** First a regression test that reproduces the failure, seen red; then the fix.

## Exempt (only these)

- Docs and README.
- Config/infra without logic (Dockerfile, compose, pyproject, CI): verified by running it.
- Spikes labelled as throwaway; they are not merged as they are.

Any other code change follows the cycle.

## Which test to write

| Marker | When |
|---|---|
| `unit` | isolated logic, no I/O |
| `integration` | several components together or a real broker (`mosquitto`) |
| `contract` | payloads/APIs against JSON Schema, OpenAPI, AsyncAPI |
| `property` | invariants with Hypothesis (scoring, curves, rng) |
| `golden` | deterministic output compared with a versioned snapshot |

Each test carries exactly one marker from this table; `conftest.py` sets `unit` by default, `integration` on the `mosquitto` ones and `contract` on those in `contracts/tests`. `mosquitto` is additional.

## Test doubles

- Only at the boundaries: HTTP, MQTT, clock, randomness.
- Hand-written fakes or dependency injection before `unittest.mock`. Model: `FakeClient` and `Harness` from `physiology/tests/test_gateway.py`.
- Never mock our own logic.
- MQTT integration uses a real mosquitto.

## Gate (everything blocking)

A single command, the same locally and in CI:

```bash
docker compose -f app/docker-compose.yml run --rm quality
```

It runs, in order: `ruff check`, `pyright`, `pytest` with coverage (lines and branches ≥ 90 %, random order, timeout), and incremental mutation (≥ 80 % on modified files). On PRs, CI adds diff coverage ≥ 90 %. A nightly runs the full mutation without blocking.

Fast loop while developing: `cd spine_monitor && uv run pytest <path> -q -p no:randomly`.

## Mutation: what to do with a survivor

A live mutant is a missing test. Write the test that kills it (red with the mutant, green without it). Do not add `# pragma: no mutate` to dodge it without consulting the maintainer.

## Warning signs — STOP

| Thought | Reality |
|---|---|
| "I'll write the code and then the tests" | No visible red, no TDD. Delete and start with the test. |
| "It's a trivial change" | Trivial follows the cycle; only the exemptions above skip it. |
| "The test passes first time" | A test that never failed proves nothing. Break it on purpose. |
| "I'll mock this function of mine to isolate it" | Boundaries only. Mocking our own logic hides bugs. |
| "Coverage is 90 %, that's enough" | Coverage measures execution, not verification. Look at mutation. |
| "I'll lower the threshold just this once" | Thresholds are not lowered without the maintainer. |
| "I'll put `# type: ignore` and move on" | Only `# pyright: ignore[rule]` with a reason. |
````

- [ ] **Step 2: Verify the skill is listed**

Run: `ls .claude/skills/tdd-workflow/SKILL.md && head -4 .claude/skills/tdd-workflow/SKILL.md`
Expected: the file exists and the frontmatter has `name` and `description`. (Test in a fresh session that `tdd-workflow` appears in the skills list.)

- [ ] **Step 3: Commit (ask the maintainer for confirmation)**

```bash
git add .claude/skills/tdd-workflow
git commit -m "Add tdd-workflow skill"
```

---

### Task 5: `quality` Docker image, `gate.sh` and compose service

**Files:**
- Create: `spine_monitor/quality/Dockerfile`
- Create: `spine_monitor/quality/gate.sh`
- Modify: `app/docker-compose.yml`

**Interfaces:**
- Produces: `docker compose -f app/docker-compose.yml run --rm quality` runs the full gate against a real mosquitto (the container shares mosquitto's network, so `127.0.0.1:1883` works without changing the tests). Variable `GATE_MUTATION` (`off` by default in this task; Task 6 gives it a value).

- [ ] **Step 1: Dockerfile**

`spine_monitor/quality/Dockerfile` (context: repo root):

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

- [ ] **Step 2: Gate script**

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

- [ ] **Step 3: Compose service**

Add to `app/docker-compose.yml`, under `services:`:

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

- [ ] **Step 4: Build and run**

Run: `docker compose -f app/docker-compose.yml --profile quality build quality`
Expected: successful build.

Run: `docker compose -f app/docker-compose.yml run --rm quality`
Expected: `==> gate passed`, and in pytest's output `1 skipped` becomes `0 skipped` (the `mosquitto` test now runs against the real broker). If `127.0.0.1:1883` on the host is already taken by another mosquitto, the `mosquitto` service does not start: stop the other one and retry.

- [ ] **Step 5: Check that the gate really fails**

Introduce a lint error (an unused `import os` in a file under `engine/src`), run the command above and see `ruff` fail; revert.
Expected: the container exits with a non-zero code at the `ruff` step.

- [ ] **Step 6: Commit (ask the maintainer for confirmation)**

```bash
git add spine_monitor/quality app/docker-compose.yml
git commit -m "Add the quality gate image, script and compose service with a real mosquitto"
```

---

### Task 6: Mutation (bounded spike and mutmut/cosmic-ray decision)

**Files:**
- Create: `spine_monitor/quality/mutation.sh`
- Modify: `spine_monitor/pyproject.toml` (config of the chosen tool)

**Interfaces:**
- Consumes: `quality` image from Task 5.
- Produces: `sh quality/mutation.sh changed|full`, which exits non-zero if the mutation score of the modified files (`changed`) is < 80 %; `full` prints the score per package and always exits 0.

**Spike context:** mutmut 3 runs in Docker with Python 3.14 and generates the mutants, but with the workspace's `src/` layout it says "could not find any test case for any mutant": the tests import the package installed in editable mode, not the copy in `mutants/`. Tried without success: `also_copy = ["engine/tests","contracts","pyproject.toml"]` and `PYTHONPATH=/tmp/sm/mutants/engine/src`.

- [ ] **Step 1: mutmut spike with a 45-minute limit**

Inside the `quality` image (`docker compose -f app/docker-compose.yml run --rm --entrypoint sh quality`), on `engine/src/spine_engine/deviation/scoring.py` (13 mutants, 63 tests). Try, in this order, until `mutmut results` shows `killed` or `survived` mutants (not `not checked`):
1. Install the members non-editable: `uv sync --frozen --all-packages --no-editable` and relaunch.
2. Remove the installed package from the import (`uv pip uninstall spine-engine`) and put `mutants/engine/src` and `mutants/contracts/src` in `PYTHONPATH`.
3. Enable `debug = true` in `[tool.mutmut]` and read which tests run.
If after 45 minutes there is no result, go to Step 2. If it works, write the final `[tool.mutmut]` in `pyproject.toml` (`source_paths`, `also_copy`, `pytest_add_cli_args_test_selection`) and skip to Step 4 with mutmut.

- [ ] **Step 2: cosmic-ray spike**

`uv add --dev cosmic-ray`. Trial config `quality/cosmic-ray.toml`:

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
Expected: a report with killed and surviving mutants. If it works, this is the tool; document the final config in `quality/cosmic-ray.toml`.

- [ ] **Step 3: If neither works**

Stop and report to the maintainer with the exact output of both; decide with them (e.g. restructure the installation). Do not invent a third route without consulting.

- [ ] **Step 4: Write `mutation.sh` (with the tool that worked)**

The script receives `changed` or `full`. In `changed`, it computes the modified `*.py` files in `*/src/**` relative to `origin/develop` (`git diff --name-only origin/develop...HEAD -- '*/src/*.py'`), runs mutation only on them and compares the score with 80 %; with no modified files, it exits 0. In `full`, it goes through all packages with code and exits 0. Write the `changed` case first with a file whose score is known (the `scoring.py` spike), see it exit 0, then force a failure by deleting an assertion in `engine/tests/test_scoring.py` and see it exit non-zero; restore.

- [ ] **Step 5: Measure the baseline**

Run: `GATE_MUTATION=full docker compose -f app/docker-compose.yml run --rm quality`
Expected: score per package. **Stop and give the maintainer the numbers**: if any package is below 80 %, decide with them (Task 11 kills survivors); do not lower the threshold.

- [ ] **Step 6: Commit (ask the maintainer for confirmation)**

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
- Consumes: the `quality` service and `GATE_MUTATION`.

- [ ] **Step 1: PR workflow**

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

- [ ] **Step 2: Nightly workflow**

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

- [ ] **Step 3: Validate syntax**

Run: `uv run --with pyyaml python -c "import yaml,sys; [yaml.safe_load(open(f)) for f in ('.github/workflows/quality.yml','.github/workflows/nightly-mutation.yml')]; print('ok')"` (from the repo root)
Expected: `ok`. The real execution is only checked when the PR is opened; tell the maintainer that the first run on GitHub is the definitive verification.

- [ ] **Step 4: Commit (ask the maintainer for confirmation)**

```bash
git add .github
git commit -m "Add the pull request quality workflow and the nightly mutation report"
```

---

### Task 8: Property-based tests (Hypothesis)

**Files:**
- Create: `spine_monitor/engine/tests/test_properties_scoring.py`
- Create: `spine_monitor/physiology/tests/test_properties_curves.py`
- Create: `spine_monitor/physiology/tests/test_properties_rng.py`

**Interfaces:**
- Consumes: `RobustZScorer.score(value: float, entry: BaselineEntry) -> float` (`entry` is only read for `median`, `mad`, `std`); `PiecewiseLinear(points)`, `.values(xs)`, `.__call__(x)`; `dose_drift(dose_ratios, drift_per_day)`; `rng.generator(seed, *keys)`, `rng.derive_uuid(seed, *keys)`.

- [ ] **Step 1: Scoring (red, then green)**

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
Expected: PASS. For each property, check that it **can** fail: temporarily change `MAD_TO_SIGMA` to `1.0` in `scoring.py` and see red in `test_score_uses_the_mad...`; restore. (This is the "red" check for a test that covers already existing code.)

- [ ] **Step 2: Curves**

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
Expected: PASS. Red check: change `1.0 - ratios` to `ratios - 1.0` in `dose_drift` and see the last property fail; restore.

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
Expected: PASS. If `test_different_branches...` fails because of a collision between keys `1` and `True`-like or `"1"` and `1`, it is a real finding: `_key_word` tags `i:`/`s:`, so it should not happen; if it does, report to the maintainer.

- [ ] **Step 4: Suite and coverage**

Run: `uv run pytest -q --cov | tail -4`
Expected: green and coverage ≥ 90 %.

- [ ] **Step 5: Commit (ask the maintainer for confirmation)**

```bash
git add spine_monitor/engine/tests/test_properties_scoring.py spine_monitor/physiology/tests/test_properties_curves.py spine_monitor/physiology/tests/test_properties_rng.py
git commit -m "Add Hypothesis property tests for scoring, curves and the rng tree"
```

---

### Task 9: Golden test for physiology

**Files:**
- Create: `spine_monitor/physiology/tests/test_golden_mission.py`
- Create: `spine_monitor/physiology/tests/golden/tiny-mission.json`

**Interfaces:**
- Consumes: `MissionGenerator(config, seed=None)`, `.iter_messages() -> Iterator[Envelope]`; `Envelope(kind, topic, payload, at, retained, astronaut_id, day)`; `MissionConfig.model_validate(raw)`; `physiology/configs/example-mission.yaml`.

- [ ] **Step 1: Write the test (red: the snapshot does not exist)**

`physiology/tests/test_golden_mission.py`:

```python
"""The tiny mission with a fixed seed reproduces a versioned snapshot (counts per kind + hash of the messages)."""

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
Expected: FAIL with `golden snapshot missing`.

- [ ] **Step 2: Generate the snapshot and review it**

Run: `UPDATE_GOLDEN=1 uv run pytest physiology/tests/test_golden_mission.py -q -p no:randomly && cat physiology/tests/golden/tiny-mission.json`
Expected: PASS and a JSON with `messages` > 0, `by_kind` with `stream_chunk`, `device_status`, `session`, etc. Check that the counts are reasonable.

- [ ] **Step 3: Green without updating, and stable between runs**

Run: `uv run pytest physiology/tests/test_golden_mission.py -q -p no:randomly` twice.
Expected: PASS both times.

- [ ] **Step 4: Check that it detects a regression**

Temporarily change `sigma` (or any noise constant) in `physiology/src/spine_physiology/signals/emg.py`, run the test and see it fail on `sha256`; restore.
Expected: FAIL with a different hash, and PASS after restoring.

- [ ] **Step 5: Stable across platforms**

Run: `docker compose -f app/docker-compose.yml run --rm quality pytest physiology/tests/test_golden_mission.py -q`
Expected: PASS on Linux with the snapshot generated on Windows. If it fails (floating-point differences between platforms), reduce `_rounded(..., digits=4)` to 3 and regenerate; if it persists, stop and report to the maintainer before deciding (e.g. generate the snapshot only in Docker).

- [ ] **Step 6: Commit (ask the maintainer for confirmation)**

```bash
git add spine_monitor/physiology/tests/test_golden_mission.py spine_monitor/physiology/tests/golden
git commit -m "Add a golden snapshot test for the seeded tiny mission"
```

---

### Task 10: Schemathesis over the engine

**Files:**
- Create: `spine_monitor/engine/tests/test_contract_openapi_fuzz.py`

**Interfaces:**
- Consumes: `create_default_app()` from `spine_engine.api`; contract `spine_monitor/contracts/openapi.yaml`. Note: the engine's handlers read the body by hand, so the OpenAPI generated by FastAPI carries no request schemas; the oracle is the contract YAML.

- [ ] **Step 1: Check the installed version's API**

Run: `uv run python -c "import schemathesis as s; print(s.__version__); print([n for n in dir(s.openapi) if not n.startswith('_')])"`
Expected: version and functions of the `openapi` namespace (in the 4.x series: `from_path`, `from_dict`, `from_asgi`, `from_url`...). Use the names it prints; if they differ from those in Step 2, adapt the test.

- [ ] **Step 2: Write the test (red if the engine API breaks the contract)**

```python
"""Fuzz of the engine's real API against the OpenAPI contract (contracts/openapi.yaml)."""

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
Expected: either PASS, or real contract failures (e.g. a 500 with an invalid body, a `Content-Type` different from the contract). Each failure is a finding: write a concrete regression test in `engine/tests/test_api.py` that reproduces it (red), fix the engine (green), and repeat until the fuzz passes. If `case.call(session=...)` does not accept a `TestClient`, use the ASGI/WSGI mechanism of the installed version (Step 1) instead of inventing one.

- [ ] **Step 3: Verify the fuzz detects a deviation**

Temporarily change the error `Content-Type` from `application/problem+json` to `application/json` in `engine/src/spine_engine/api/problems.py`, run and see it fail; restore.
Expected: FAIL and then PASS.

- [ ] **Step 4: Honest scope**

backend and app do not have an implemented API yet (`backend/src/monitor/api.py` is empty; `app-openapi.yaml` is implemented by another issue). Leave one line in the skill/README: "add the same Schemathesis test when implementing those APIs".

- [ ] **Step 5: Commit (ask the maintainer for confirmation)**

```bash
git add spine_monitor/engine
git commit -m "Fuzz the engine API against its OpenAPI contract with Schemathesis"
```

---

### Task 11: Kill mutation survivors (data-driven)

**Files:**
- Modify: tests in `contracts/tests`, `engine/tests`, `physiology/tests` according to the report.

**Interfaces:**
- Consumes: `GATE_MUTATION=full` from Task 6.

This task cannot carry the test code in advance: the survivors come from the report. The procedure is fixed and each cycle follows TDD.

- [ ] **Step 1: Report**

Run: `GATE_MUTATION=full docker compose -f app/docker-compose.yml run --rm quality`
Expected: list of surviving mutants per file. Sort by package and start with those with the lowest score.

- [ ] **Step 2: One cycle per survivor**

1. Read the mutant (the line and the change, e.g. `<` → `<=`).
2. Write a test that passes with the original code and fails with the mutant. Example for `scoring.py`: if the change of `_ABSOLUTE_FLOOR = 1e-6` to `1e-5` survives, add in `engine/tests/test_scoring.py`:

```python
def test_a_scale_just_above_the_floor_is_used_as_is():
    entry = SimpleNamespace(median=0.0, mad=0.0, std=5e-6)
    assert RobustZScorer().score(1.0, entry) == pytest.approx(1.0 / 5e-6)
```

3. See the test pass with the original and fail with the mutant (`mutmut`/`cosmic-ray` confirm it when re-running that file).
4. If the mutant is equivalent (does not change observable behaviour), document it in one line in the report to the maintainer; do not add exclusions without their approval.

- [ ] **Step 3: Parallelise by package**

Launch `sonnet` subagents (medium effort), one per package (`contracts`, `engine`, `physiology`), with this prompt: "Kill the surviving mutants of `<package>` following the cycle of Step 2 of Task 11 of the plan. One test per survivor or coherent group, in the module's existing test file. Return the score before/after and the equivalent mutants." Review each one's real diff.

- [ ] **Step 4: Verify the threshold**

Run: `GATE_MUTATION=full docker compose -f app/docker-compose.yml run --rm quality`
Expected: score ≥ 80 % per package. If a package does not get there after accounting for the equivalents, report the numbers to the maintainer; do not lower the threshold.

- [ ] **Step 5: Commit per package (ask the maintainer for confirmation)**

```bash
git add spine_monitor/<package>/tests
git commit -m "Kill surviving mutants in <package>"
```

---

### Task 12: Integrate `implement-feature` with `tdd-workflow`

**Files:**
- Modify: `.claude/skills/implement-feature/SKILL.md`

**Interfaces:**
- Consumes: skill `tdd-workflow` (Task 4), `quality` service (Task 5).

- [ ] **Step 1: Phase 4**

In the "Phase 4 — Implementation" section, replace the sentence "Use the skills/agents identified in Phase 1 (e.g. `superpowers:test-driven-development`, `superpowers:subagent-driven-development`)." with:

```
Use the skills/agents identified in Phase 1 (e.g. `superpowers:subagent-driven-development`). **Every code implementation and bugfix follows `tdd-workflow`**: each subagent receives its cycle (visible red, green, refactor) and must return the test output in red and in green, not just the summary. Without a visible red, the part is rejected.
```

- [ ] **Step 2: Phase 5**

In "Phase 5 — Verification", replace the first point ("Run the repo's tests, lint and build; if there are none, actually run the feature (skill `run` if applicable).") with:

```
- Run the full gate: `docker compose -f app/docker-compose.yml run --rm quality` (ruff, pyright, tests with coverage ≥ 90 %, incremental mutation ≥ 80 %). If Docker is not available, stop and tell the maintainer; do not replace it with a partial run without saying so. For code outside `spine_monitor` (its own build/tests), also run the commands you discover in the repo.
- Check that each part has its test seen in red and that no threshold has been lowered.
```

And add to the "Warning signs — STOP" table the row:

```
| "Tests pass locally, I'm not running the gate" | The Docker gate is the criterion; local is not enough. |
```

- [ ] **Step 3: Check coherence**

Run: `grep -n "tdd-workflow\|run --rm quality" .claude/skills/implement-feature/SKILL.md`
Expected: they appear in Phase 4, Phase 5 and the table.

- [ ] **Step 4: Commit (ask the maintainer for confirmation)**

```bash
git add .claude/skills/implement-feature/SKILL.md
git commit -m "Make implement-feature use tdd-workflow and the quality gate"
```

---

### Task 13: Documentation and vault

**Files:**
- Modify: `spine_monitor/README.md`, `spine_monitor/README.es.md`, `spine_monitor/README.ca.md`

- [ ] **Step 1: Quality section in the three READMEs, in sync**

Add to each one a section (in its language; keep the existing language-switch link) with: the gate command, the fast loop `uv run pytest <path> -q -p no:randomly`, the thresholds (coverage ≥ 90 %, mutation ≥ 80 % on modified files), the markers, and the note that in CI diff coverage also requires ≥ 90 %. No references to the vault.

Text in English (`README.md`); translate faithfully to `README.es.md` (Spanish) and `README.ca.md` (Catalan):

```markdown
## Quality and TDD

Every code change follows TDD (see the `tdd-workflow` skill) and passes the quality gate:

    docker compose -f app/docker-compose.yml run --rm quality

It runs ruff, pyright, the tests with coverage (lines and branches ≥ 90 %, random order, real mosquitto) and incremental mutation (≥ 80 % on the modified files). On PRs, CI also requires diff coverage ≥ 90 %; a nightly generates the full mutation report.

Fast loop: `cd spine_monitor && uv run pytest <path> -q -p no:randomly`.

Each test carries a marker: `unit`, `integration`, `contract`, `property` or `golden` (`mosquitto` is additional).
```

- [ ] **Step 2: Vault (read first, write after)**

With the vault's MCP tools: `search_vault` for "testing" in `projects/Asclepius Orbit` to avoid duplicating; `log_decision` with the decision (TDD with visible red; gate in Docker; thresholds; pyright; chosen mutation tool and why; exceptions) and `create_note` with the testing strategy note in `projects/Asclepius Orbit/`. If an existing note is updated, `update_note` **overwrites the whole note**: read it and resend the complete content. Ask the maintainer for confirmation before writing.

- [ ] **Step 3: Final verification**

Run: `docker compose -f app/docker-compose.yml run --rm quality`
Expected: `==> gate passed`.

Run: `git status --short && git log --oneline -15`
Expected: clean tree except what the maintainer decides; commits without `Co-Authored-By` lines.

- [ ] **Step 4: Docs commit (ask the maintainer for confirmation)**

```bash
git add spine_monitor/README.md spine_monitor/README.es.md spine_monitor/README.ca.md
git commit -m "Document the TDD workflow and the quality gate in the READMEs"
```

---

### Task 14: Faster mutation on re-runs (bounded spike, requested by the maintainer)

**Files:**
- Modify: `spine_monitor/quality/mutation.sh`, `app/docker-compose.yml`, `.github/workflows/quality.yml`, `.github/workflows/nightly-mutation.yml` (only what the measurement justifies)

**Interfaces:**
- Consumes: `sh quality/mutation.sh changed|full` and `MUTATION_STAGE`, `MUTATION_PACKAGES`, `MUTATION_BASE` from Task 6.
- Produces: times measured before and after; optionally `MUTATION_JOBS` (mutmut child processes), a named Docker volume for mutmut's working directory, and a CI cache.

**Measured context:** the full physiology run takes 108 min on an idle container; `mutation.sh` prepares its copy in `/tmp/mutation` and discards it with `--rm`, so every run starts from scratch. Launching about 5 mutation containers in parallel makes the clean-tests phase hit the timeout and abort the run. Constraints: do not trim mutants or code, do not lower the 80 % threshold, each file's mutation score must not change because of the optimisation.

- [ ] **Step 1: Timing baseline.** On an idle container, noting `nproc`, measure `sh quality/mutation.sh changed physiology/src/spine_physiology/signals/stretch.py` (385 mutants) and `full` of the `engine` package. Record seconds, mutants and mutants per second.
- [ ] **Step 2: Parallelism.** Read how `mutation.sh` invokes `mutmut run` and what mutmut 3.8.1 supports (`mutmut run --help`, child processes). Try increasing values on the same file; record time and confirm that the score does not change and that the clean-tests phase does not time out.
- [ ] **Step 3: Persistent state.** Mount a named Docker volume at `MUTATION_STAGE`. Run the same file three times: unchanged, changing the body of a `src` function, and changing only a test. Record how long each takes and whether mutmut reuses the results of the mutants already evaluated. If it does not reuse them, say so with the evidence and do not continue with the cache.
- [ ] **Step 4: Physiology profile.** Measure mutants per second per module of `physiology/src` to know which modules dominate and propose groups for a CI matrix.
- [ ] **Step 5: Implement only what was measured as useful.** `MUTATION_JOBS` in `mutation.sh` with a safe default; named volume in the `quality` service if Step 3 justifies it; `actions/cache` (action pinned to a major version) keyed by the hash of each package's `src` and `tests`, and a per-package matrix in the nightly, if Steps 3 and 4 justify them.
- [ ] **Step 6: Verification.** Repeat the Step 1 measurements with the changes and show the improvement with numbers. Check that the per-file score is the same and that the pass/fail check of `changed` still works (a weakened test makes it exit non-zero). Validate the YAML.
- [ ] **Step 7: Commit (ask the maintainer for confirmation)**

```bash
git add spine_monitor/quality app/docker-compose.yml .github
git commit -m "Speed up repeated mutation runs with parallel mutmut jobs and a persistent state"
```

**Result (2026-11-04, measured, not adopted):** there is no code commit. mutmut 3.8.1 already uses all cores by default (`max_children` = 22): with 8, 11 and 33 processes `stretch.py` (385 mutants) takes 438, 466 and 449 s versus 367-381 s by default. Two identical runs change the verdict of about 3 mutants (92.5 % versus 91.7 %). A cache of `mutants/` in a volume reuses results (unchanged re-run 136 s, a function change 186 s), but mutmut does not invalidate the state when only the tests change: with all the `stretch` tests disabled the cache still gave 92.5 % when the real result was 83.1 %, so it would let `changed` pass with weakened tests. Safe option not built or measured: `actions/cache` keyed by the exact hash of src, tests, `pyproject.toml` and `uv.lock`, without restore-keys (it only speeds up identical re-runs). The time of the full run only drops by splitting physiology by modules in a CI matrix (physiology is about 6051 of about 7480 lines of source code); that split is not measured.

---

### Tasks 15 to 17: maintainer decisions after the final review (2026-11-04)

- **Task 17 — 422 contract.** Document in `contracts/openapi.yaml` that `POST /baseline` and `POST /assess` can return 422 `insufficient_data` with a valid request, and remove the exclusion of the ST202 check from the Schemathesis fuzz (`engine/tests/test_contract_openapi_fuzz.py`). The maintainer notifies the team of the contract change.
- **Task 15 — Mutation only of the touched functions.** Reason: mutation times are measured on 22 CPUs and a GitHub runner for a private repo has 2 vCPU; `changed` mutates whole files and the first PR of this branch (10 physiology files) would exceed the 120 min timeout. `changed` must mutate only the functions in the diff (with mutmut 3.8.1, which identifies mutants by function), with measurement before and after, without lowering the 80 % threshold or trimming mutants. The full nightly on a 2 vCPU runner remains unresolved.
- **Task 16 — Log-argument mutants.** Exclude through mutmut configuration, without `pragma: no mutate` in the code, only if a clean, measured mechanism exists; otherwise report and do nothing.
- Decision on branch protection (I1): accept without protection; the CI check reports but does not block.

**Results (2026-11-04):**
- **Task 17:** only `POST /baseline` can return 422 `insufficient_data` (the only `raise` in `baseline/builder.py`); it was documented in `openapi.yaml` without bumping the version. The exclusion of the ST202 check from the fuzz is kept because Schemathesis 4.33 does not read the documented responses in that check.
- **Task 15:** `changed` mutates only the functions and methods touched by the diff. A one-function diff (`resistance_to_adc`): 34 mutants in 171 s versus 385 mutants and ~395 s for the whole of `stretch.py`. The whole diff of this branch: 1541 mutants instead of 7840, 796-880 s and 92 % on 22 CPUs; on a 2 vCPU runner it is estimated (extrapolated, not measured) at about 2 h, at the edge of the 120 min timeout. False "killed" results in the engine package were fixed (pytest cannot select Schemathesis tests by id and exited with 4, which mutmut counts as killed): engine's real score is 83.4 % (546/655), not the 94.8 % it gave. No minimum-sample rule was added: a mutant that changes verdict moves the score by 100/N points.
- **Task 16:** mutmut 3.8.1's `do_not_mutate_patterns` skips the arguments of log calls (`log`/`_log`/`logger`/`_logger`/`logging` plus `debug|info|warning|warn|error|exception|critical|log`) without a `pragma` in the code: 125 of the 8993 physiology mutants (all from `gateway/app.py`), not ~150. Verified by comparing the full lists before and after. It also excludes 7 log mutants that are killed today.
- **Later maintainer decisions (Task 18):** the PR job goes up to 240 min; the full mutation workflow becomes manual (`workflow_dispatch`, with an optional `packages` input) because a full physiology run (~108 min on 22 CPUs) fits neither on a 2 vCPU runner nor in the 360 min maximum, and it is run locally with `GATE_MUTATION=full`; `changed` prints the score but does not fail when the selection has fewer than 10 mutants (`MUTATION_MIN_SAMPLE`, 0 disables it), a deliberate rule that lets small changes through without their tests killing the mutants; `MUTATION_MIN_SAMPLE` and `MUTATION_THRESHOLD` are validated at the start (exit 2 if non-numeric). No branch protection in the repo (free plan): the check reports but does not block.

---

## Self-review against the spec

| Spec requirement | Task |
|---|---|
| `tdd-workflow` skill | 4 |
| Integration with `implement-feature` | 12 |
| Config in pyproject (testpaths, markers, coverage, ruff, types) | 1, 2, 3 |
| Blocking gates | 5 (gate), 6 (mutation), 7 (diff coverage) |
| Docker `quality` with mosquitto | 5 |
| GitHub Actions (PR and nightly) | 7 |
| Contracts: Schemathesis | 10 |
| Contracts: MQTT/AsyncAPI payloads and spec↔model sync | already exist (baseline); labelled `contract` by `conftest.py` in Task 1 |
| Property-based | 8 |
| Golden | 9 |
| Fault injection | largely covered by existing tests; new gaps via mutation in Task 11 |
| Prior checks (mutation, types, Schemathesis) | 3 (pyright chosen), 6 (spike), 10 (Step 1) |
| Baseline and thresholds without lowering them | 1 (Step 7), 6 (Step 5), 11 |
| Documentation and vault | 13 |

**Deviations from the spec, by maintainer decision or by evidence:**
- Type checker: pyright, and the ~200 errors are fixed now with subagents (maintainer's decision).
- Schemathesis only over the engine until backend and app have an API.
- Fault injection: no new tests are invented; mutation indicates the real gaps.
