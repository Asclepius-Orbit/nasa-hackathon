---
name: agent-evals
description: Use when writing, changing or running evals for the LLM agent in `spine_monitor/agent` (pydantic-evals), or when changing its system prompt, the model or the MCP tools it uses.
---

# agent-evals

Regression evals of the agent's behaviour with `pydantic-evals` (**dev** dependency in `spine_monitor/pyproject.toml`, pinned in `uv.lock`; v2.66). Every code change follows `tdd-workflow` (it is not exempt): the new eval is seen red before touching the prompt or the agent. For Claude models and parameters load `claude-api`; for the tools, `spine_monitor/mcp_server/README.md`.

## What to check (one Case per behaviour)

| Behaviour | How |
|---|---|
| Uses the right tools with valid arguments (`astronaut_id`, RFC 3339 with offset, `limit` 1-500, `project_days` 1-365) | Deterministic evaluator over the list of calls |
| Never diagnoses or prescribes | Deterministic (list of forbidden phrases) + live `LLMJudge` |
| Safe language: on worrying signals it refers to a healthcare professional | `Contains`/regex + live `LLMJudge` |
| Does not invent data: every figure in the answer comes from a tool result; with no data it says there is none (`null`, never 0) | Deterministic: compares numbers in the text with those from the tools |
| Refuses what is out of scope (writing data, diagnosing, non-medical topics) and calls no tool | Deterministic (zero calls) + refusal text |
| `simulate_scenario` is presented as hypothetical | `Contains` / judge |

The 7 tools are read-only/simulation: an eval that sees a call outside those 7 fails.

## Verified API (pydantic-evals 2.66)

- `from pydantic_evals import Case, Dataset`; `from pydantic_evals.evaluators import Evaluator, EvaluatorContext, EvaluationReason, Contains, MaxDuration, LLMJudge, MaxToolCalls`.
- `Case(name=, inputs=, expected_output=, metadata=, evaluators=[...])`.
- `Dataset[In, Out, Meta](name="...", cases=[...], evaluators=[...])`: **`name` is mandatory**.
- An evaluator is a function `(ctx: EvaluatorContext) -> bool | float | str | EvaluationReason` or a `@dataclass` inheriting from `Evaluator` with `evaluate(self, ctx)`. `ctx` has `inputs`, `output`, `expected_output`, `metadata`, `duration`.
- `report = dataset.evaluate_sync(task, progress=False)`; `report.cases[i].assertions[name].value`, `report.failures`, `report.averages().assertions` (1.0 = everything passes), `report.print()`.
- `ToolCorrectness`, `TrajectoryMatch`, `ArgumentCorrectness`, `MaxToolCalls` **read OpenTelemetry spans** and only see tools executed by pydantic-ai. The agent's README says Ollama or llama.cpp, not pydantic-ai: do not use them unless the agent is built with pydantic-ai. Default alternative: the task returns an `AgentRun(text, tool_calls=[(name, args)])` dataclass and your evaluators read `ctx.output`.
- `LLMJudge(rubric=..., model=..., include_input=True)`: if you do not pass `model`, it uses `openai:gpt-5.2`. Always pin the model (`model=` or `set_default_judge_model`).

## No network by default

1. The `task` receives an **injected answer source**: a hand-written fake (script of tool calls and final text per Case) or recorded answers in versioned JSON. The boundary = the LLM; the MCP server runs for real in memory with the `mcp_server` mocks (`create_server(app, simulator, base)`) to validate the arguments against the real schema. No `unittest.mock` over our own logic.
2. The offline pytest checks the **dataset and the evaluators**: good cases pass, bad cases (an answer that diagnoses, an invented tool, an invented figure) fail. Without that, a broken evaluator gives a false green.
3. If you use `pydantic_ai`, block the network in the test: `pydantic_ai.models.ALLOW_MODEL_REQUESTS = False` (or `override_allow_model_requests(False)`), and `TestModel`/`FunctionModel` models from `pydantic_ai.models`.
4. `LLMJudge` never runs offline: a fake judge proves nothing. Offline uses only deterministic evaluators.

## Where they live and markers

- Evaluators, datasets and runner: `spine_monitor/agent/evals/` (outside `agent/src`: `pydantic-evals` is dev and must not reach production; outside `testpaths`, coverage and pyright).
- Offline tests: `spine_monitor/agent/tests/test_evals_*.py`, with the existing markers: `unit` (default) for evaluators and fakes; `golden` if you compare with a versioned recording or snapshot. **No new marker is added.**
- Evals with a real model: manual opt-in, **outside pytest and the gate/CI**: `agent/evals/run_live.py` (script), which aborts unless `SPINE_AGENT_EVALS_LIVE=1` and takes the model from another environment variable. Only if it must run with pytest, an `integration` test with `skipif` on that variable. Never in `GATE_MUTATION` nor in workflows.
- Offline command: `cd spine_monitor && uv run pytest agent -q -p no:randomly`. Full gate: the one in `tdd-workflow` (coverage ≥ 90 %, mutation on what changed).
- Datasets as YAML/JSON (`Dataset.from_file` / `to_file`) or in Python; version them with the code. No real astronaut data: use the examples in `contracts/examples/valid/`.

## Changing the prompt without breaking evals

1. Run the evals offline first: green baseline.
2. First write the Case for the new or broken behaviour; see it red (`tdd-workflow`).
3. Change the prompt. One change per commit, to attribute regressions.
4. Offline green and, together with the maintainer, a manual live run: compare `report.averages()` before and after; do not lower an evaluator's bar to make it pass.
5. Changing the model or parameters counts as a prompt change. If an MCP tool changes (name, parameters), update the Cases first.

## Warning signs — STOP

| Thought | Reality |
|---|---|
| "I'll test with the real model, that's what matters" | It costs money and is not deterministic. Offline in the gate; live only manual and opt-in. |
| "I'll put it in CI, it's cheap" | Live evals never go to the gate or CI. |
| "I'll leave `LLMJudge` without `model`" | It uses `openai:gpt-5.2` by default: unexpected network and cost. |
| "I'll use `ToolCorrectness` and done" | It needs pydantic-ai OTel spans; with another runtime it always fails. |
| "All evals pass first time" | Add a case that must fail; an evaluator that never failed proves nothing. |
| "I'll relax the forbidden phrase so it passes" | Change the prompt, not the evaluator, unless the maintainer decides otherwise. |
| "I'll put the evaluator in `agent/src`" | `pydantic-evals` is dev; it lives in `agent/evals/`. |
| "I'll add an `eval` marker" | `conftest.py` requires a kind marker; use `unit`/`golden`/`integration`. |
