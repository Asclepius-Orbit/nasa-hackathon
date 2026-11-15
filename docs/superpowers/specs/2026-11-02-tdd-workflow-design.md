# TDD workflow and quality gates

Date: 2026-11-02. Status: approved in chat, pending document review.

## Goal

Everything implemented in the project follows TDD and passes a set of automatic quality gates, identical locally and in CI. A project skill describes the process; the configuration and CI enforce it.

## Scope

The six members of the `spine_monitor` uv workspace: `contracts`, `physiology`, `engine`, `mcp_server`, `agent`, `backend`. All in a single delivery, on the current branch.

## Deliverables

### 1. Skill `.claude/skills/tdd-workflow/SKILL.md` (versioned)

Content:

- **Mandatory cycle.** Red, green, refactor.
  - Visible red: the test is run and shown failing for the right reason before any production code is written.
  - Green with the minimal change; the green run is shown.
  - Refactor only with the suite green.
- **Bugfix.** A regression test that reproduces the failure, seen red, before the fix.
- **Exceptions.** Only: docs and README; config/infra without logic (Dockerfile, compose, pyproject, CI), which is verified by running it; spikes labelled as throwaway and not mergeable as they are. Every code change outside that follows the cycle.
- **Test double policy.** Doubles only at the boundaries: HTTP, MQTT, clock and randomness. Hand-written fakes or dependency injection are preferred over `unittest.mock`. Our own logic is never mocked. MQTT integration uses a real mosquitto.
- **Markers.** `unit`, `integration`, `contract`, `property`, `golden` and the existing `mosquitto`. Each test carries exactly one of the first five.
- **What to run and when.** Exact commands of the `quality` service.
- **Warning signs.** A table of rationalisations versus reality, in the style of `implement-feature`.

### 2. Integration with `implement-feature`

- Phase 4: invoke `tdd-workflow` for every implementation.
- Phase 5: run the full gate (`docker compose run quality`) as verification evidence; the success criterion is the gates in section 4.

### 3. Configuration in `spine_monitor/pyproject.toml`

- `testpaths` with the six members.
- Registered markers and `--strict-markers`.
- Development dependencies: `pytest-cov`, `pytest-randomly`, `pytest-timeout`, `hypothesis`, `schemathesis`, `diff-cover`, ruff, type checker, mutation tool.
- Coverage with branches enabled and `fail_under = 90`.
- Ruff and type checker configured.

### 4. Blocking gates

| Gate | Threshold |
|---|---|
| Ruff | no errors |
| Type checker | no errors |
| Tests | all green, in random order, with timeout |
| Coverage (lines and branches) | ≥ 90 % |
| Diff coverage on PRs | ≥ 90 % on changed lines |
| Incremental mutation on PRs | ≥ 80 % on modified files |
| Contract tests | all green |

The full nightly mutation run produces a report and does not block.

### 5. Docker

A `quality` service in compose, next to mosquitto (with its healthcheck). A single command runs lint, types, tests with coverage, contracts and mutation. Local and CI use the same command and the same image. Linux image, so the mutation tool works even if the host is Windows.

### 6. GitHub Actions

- PR to `develop`: lint, types, tests with coverage, diff-cover, contracts, incremental mutation.
- Nightly: full mutation over the whole repo, with a report and without blocking.

### 7. Tests on the existing code

- **Contract:** Schemathesis against the OpenAPI specs (engine, backend, app); MQTT payloads validated against `asyncapi.yaml` and the topics; sync tests between schemas, Pydantic models and specs, extending the existing drift test.
- **Property-based (Hypothesis):** engine (scoring, detectors) and physiology (generators, curves).
- **Golden:** physiology output with a fixed seed compared with a versioned snapshot.
- **Fault injection:** broker down, duplicated or out-of-order messages, corrupt payloads and timeouts in gateway, backend and mcp_server.

## Mandatory prior checks

Nothing is fixed without evidence executed in the repo with Python 3.14:

1. Mutation tool: try mutmut inside Docker and cosmic-ray; pick the one that works.
2. Type checker: try pyright, mypy and ty; pick the one that works best.
3. Schemathesis against the current FastAPI apps.

## Baseline and thresholds

Before enabling the thresholds, the current coverage and mutation score are measured. If the existing code does not reach 90 % or 80 %, the numbers are reported to the maintainer and decided with them; thresholds are not lowered unilaterally. Packages without real code (`agent`, `mcp_server`) get the threshold from their first code.

## Execution order

1. Prior checks and baseline.
2. Skill and configuration.
3. Docker `quality` and mosquitto.
4. GitHub Actions.
5. Contract, property-based, golden and fault-injection tests, package by package.
6. Integration with `implement-feature`.

## Out of scope

- End-to-end tests with real hardware.
- Pact (consumer-driven), architecture tests, local pre-commit and separate red/green commits: explicitly discarded.
- Subdirectories per test type: tests stay flat and are distinguished by marker.
