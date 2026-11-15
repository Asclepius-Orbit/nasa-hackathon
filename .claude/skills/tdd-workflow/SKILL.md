---
name: tdd-workflow
description: Use when writing or changing production code (feature, bugfix, refactor) in this repo, before writing the first line of code.
---

# tdd-workflow

Every code change follows TDD and passes the quality gate. **Violating the letter of the cycle is violating its spirit.**

## Cycle (mandatory)

1. **Red.** Write a test that describes the behaviour. Run it and **show the failure**; check that it fails for the right reason (an expected assertion or import, not a syntax or fixture error). Without a visible red there is no code.
2. **Green.** Write the minimal change. Run and **show the green**.
3. **Refactor.** Only with the suite green; run again after each step.
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

The engine is already fuzzed with Schemathesis against `contracts/openapi.yaml` (`engine/tests/test_contract_openapi_fuzz.py`); when implementing the backend and app APIs, add the same Schemathesis test.

## Supporting skills

Depending on what the change touches, also load (full table in `.claude/skills/ROUTING.md`): `property-based-testing` for invariants and round trips (marker `property`), `python-testing-patterns` for fixtures and parametrization, `async-python-patterns` if there is asyncio, `python-type-safety` if pyright complains. This file rules over them on markers, doubles and thresholds.

## Test doubles

- Only at the boundaries: HTTP, MQTT, clock, randomness.
- Hand-written fakes or dependency injection before `unittest.mock`. Model: `FakeClient` and `Harness` from `physiology/tests/test_gateway.py`.
- Never mock your own logic.
- MQTT integration uses a real mosquitto.

## Gate (everything blocking)

Single command, the same locally and in CI:

```bash
GATE_MUTATION=changed docker compose -f app/docker-compose.yml run --rm quality
```

It runs, in order: `ruff check`, `pyright`, `pytest` with coverage (lines and branches ≥ 90 %, random order, timeout), and incremental mutation (≥ 80 % on the modified functions and methods). Without `GATE_MUTATION=changed` mutation is skipped (fast loop). `changed` compares against `origin/develop` by default (`MUTATION_BASE` changes it) and only counts committed changes: commit before running it. If `changed` scores fewer than 10 mutants (`MUTATION_MIN_SAMPLE`, 0 disables it) and falls below 80 %, it shows the score but does not fail (maintainer's decision: with so few mutants the score is noise). On PRs, CI adds diff coverage ≥ 90 %. Full mutation is run by hand: locally with `GATE_MUTATION=full` (there is also a manual workflow), not in a nightly.

Fast loop while developing: `cd spine_monitor && uv run pytest <path> -q -p no:randomly`.

## Mutation: what to do with a survivor

A living mutant is a missing test. Write the test that kills it (red with the mutant, green without it). Do not add `# pragma: no mutate` to avoid it without consulting the maintainer.

## Warning signs — STOP

| Thought | Reality |
|---|---|
| "I'll write the code and then the tests" | Without a visible red there is no TDD. Delete and start with the test. |
| "It's a trivial change" | Trivial follows the cycle; only the exemptions above are skipped. |
| "The test passes first time" | A test that never failed proves nothing. Break it on purpose. |
| "I'll mock this function of mine to isolate" | Only boundaries. Mocking your own logic hides bugs. |
| "Coverage is 90 %, enough" | Coverage measures execution, not verification. Look at mutation. |
| "I'll lower the threshold just this once" | Thresholds are not lowered without the maintainer. |
| "I'll put `# type: ignore` and move on" | Only `# pyright: ignore[rule]` with a reason. |
