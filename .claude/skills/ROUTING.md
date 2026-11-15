# Repo skill routing

Single table used by `implement-feature`, `tdd-workflow`, `review-pr` and `receive-review`. When a repo skill fits the task, the orchestrator names it in the plan or in the subagent prompt, and the subagent loads it with the `Skill` tool **before starting**. Subagents do not inherit skills: if the prompt does not name them, they do not use them.

"Relevant" = the row's trigger matches. Do not load a skill just in case, and do not load them all.

## Table

| Trigger (what the task touches) | Skill(s) | What for |
|---|---|---|
| Any Python production code (feature, bugfix, refactor) | `tdd-workflow` | Red/green/refactor cycle and gate. Always. |
| New or changed pytest tests: fixtures, parametrize, mocks | `python-testing-patterns` | Fixture and test-double patterns. `tdd-workflow` rules (markers, doubles only at boundaries). |
| Invariants, round trips (serialize/parse), normalizers, validators, numerics, scoring, curves, rng in `physiology`, `engine`, `contracts` | `property-based-testing` | Hypothesis properties (marker `property`). |
| `async def`, `asyncio`, aiomqtt, async httpx, async FastAPI, tasks and cancellation | `async-python-patterns` | Correct concurrency, without blocking the loop. |
| Annotations, generics, Protocols (`ports.py`), pyright errors | `python-type-safety` | Strict typing. Only `# pyright: ignore[rule]` with a reason. |
| `pyproject.toml`, dependencies, uv, ruff, scripts, workspace | `modern-python` | uv and ruff conventions. **Ignore** its advice on `ty`, prek and the shims: this repo uses pyright. |
| `mcp_server`: add or change an MCP tool (parameters, output, errors) | `mcp-tool-change` + `mcp-builder` | Repo steps (port, mock, tests, READMEs, count) and tool design criteria. |
| LLM agent (`agent`): prompt, model, the tools it uses or its evals | `agent-evals` | Regression evals with pydantic-evals, offline by default. |
| Calls to the Claude API | `claude-api` (user-level) | Models, parameters, caching. |
| UI (`app/ui`): screens, components, styles | `frontend-design`, `impeccable`, `ui-ux-pro-max` | Visual direction, UX, accessibility. |
| UI: component tests or telemetry charts | `vitest`, `uplot` (dependencies, not skills) | TS tests and series streaming. |
| Looking up a library's documentation (FastAPI, aiomqtt, `mcp`, numpy, pydantic) | MCP `context7` | Current docs; the model may be out of date with Python 3.14. |
| Bug or failing test with no clear cause | `superpowers:systematic-debugging` | Root cause before fixing. |
| Design of a new feature or behaviour yet to be defined | `superpowers:brainstorming` | Before planning. |
| JSON Schema / OpenAPI / AsyncAPI contracts, examples, generated models or TS types | `contract-first` | Order: contract, examples, regenerate, contract tests, drift. |
| Model, signal, feature, curve, fault, scenario or scoring in `physiology` / `engine`; the golden changes | `physiology-engine` | Seeded determinism, units, invariants, golden. |
| MQTT: Mosquitto broker, topics, gateway, backend ingest, tests with a broker | `mqtt-dev` | Local broker, inspection, topic convention and tests. |

## Rules

1. **The repo's conventions rule** (`tdd-workflow`, README, `pyproject`, pyright, thresholds) over any advice from a third-party skill. If they clash, the repo wins and the clash is noted.
2. **Third-party skills** (`mcp-builder`, `property-based-testing`, `modern-python`, `async-python-patterns`, `python-testing-patterns`, `python-type-safety`): read them as reference, do not run their scripts without reviewing them (see `THIRD_PARTY.md`).
3. **A subagent prompt** that uses skills includes the line: "Before starting, invoke with the Skill tool: `<skill>`, `<skill>`. If they clash with `tdd-workflow` or the README, the repo rules."
4. **Node: pnpm only.** Never `npm` or `npx`: `pnpm install`, `pnpm run <script>`, `pnpm dlx <package>` (and `pnpm add -D --save-exact`). Lockfile: `pnpm-lock.yaml`. If a third-party skill says `npm`/`npx`, translate it.
5. **New skill in the repo**: add its row here, in the same commit.
