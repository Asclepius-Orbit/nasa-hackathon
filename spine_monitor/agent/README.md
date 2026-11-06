**English** · [Español](README.es.md) · [Català](README.ca.md)

# spine-agent

Local LLM agent that answers questions about an astronaut's spine-health status using the tools of the MCP server (`spine_monitor/mcp_server`). It runs fully offline against a local [Ollama](https://ollama.com) model, streams the answer token by token, and answers in the language of the question (English, Spanish or Catalan).

Rules it follows, by design:

- **Read-only.** It cannot change data, the traffic light or send reports; it refuses those requests without calling any tool.
- **It does not diagnose.** No medical diagnosis and no treatment advice; clinical questions go to the flight surgeon.
- **It says "I don't know"** when a tool returns no data or an error. It never invents numbers (a missing value is "not available", never 0).
- **If the LLM fails, nothing else does.** The agent is a separate process: the direct app-engine path does not depend on it. If Ollama is down, times out or answers nothing, the agent prints a deterministic summary built from the MCP tools instead (no LLM involved).

These rules are enforced in three layers: the system prompt, a code post-filter (`guards.py`) that appends a correction notice if an answer asserts a diagnosis, and the evals below.

## How it works

```
spine-agent (CLI, streaming chat)
  └─ pydantic-ai Agent ── OllamaModel ──► Ollama (OpenAI-compatible API, http://localhost:11434/v1)
        └─ MCP toolset (stdio) ──► python -m spine_mcp_server   (7 read-only tools)
  └─ if the model fails: McpStatusSource ──► get_current_status + explain_assessment ──► template
```

Thinking mode is disabled (it breaks tool calling on Qwen 3.5 in Ollama), the temperature is 0.2 and Ollama runs with a 16k context (its default of 4k silently truncates the tool schemas).

## Quick start

Needs Docker. Ollama runs in Docker under the `llm` compose profile, and the models are **cached in the `ollama-models` volume**: they are downloaded once, and later starts need no network.

```bash
# From the repository root. First time: downloads qwen3.5:4b (3.3 GB) into the volume.
docker compose -f app/docker-compose.yml --profile llm up -d

# Chat (streaming, multi-turn). Exit with /exit, /quit, Ctrl-D or Ctrl-C.
cd spine_monitor
uv run spine-agent --astronaut AST-01
```

`AST-01` is the mock astronaut that is amber; try `Why am I amber?`. `--astronaut` is required. `--model` overrides the model.

- `ollama-pull` downloads only the models of `SPINE_AGENT_LLM_MODELS` (space-separated tags, default `qwen3.5:4b`) that are missing from the volume, so repeating `up` is instant. Compose reads `.env` from `app/`: copy `.env.example` to `app/.env` to change it (the example lists the three benchmarked models, about 16 GB).
- `docker compose ... down` keeps the models; `down -v` deletes the volume and forces a new download.
- Ollama listens only on `127.0.0.1:11434` and is pinned to version 0.40.14.

### Settings (`SPINE_AGENT_*` environment variables or `.env`)

| Variable | Default | Meaning |
|---|---|---|
| `SPINE_AGENT_OLLAMA_BASE_URL` | `http://localhost:11434/v1` | OpenAI-compatible endpoint of Ollama |
| `SPINE_AGENT_MODEL` | `qwen3.5:4b` | Model tag |
| `SPINE_AGENT_TEMPERATURE` | `0.2` | Between 0 and 2 |
| `SPINE_AGENT_REQUEST_TIMEOUT_S` | `60` | Model request timeout, in seconds |
| `SPINE_AGENT_MCP_COMMAND` / `SPINE_AGENT_MCP_ARGS` | this Python / `["-m","spine_mcp_server"]` | How the MCP server is launched (args as JSON) |
| `SPINE_AGENT_MCP_LOG_FILE` | none | Where the MCP server log goes; discarded by default so it does not mix with the chat |

## Evals

- **Offline (part of the quality gate):** 28 cases (16 English, 6 Spanish, 6 Catalan) in `evals/dataset.py`, run with a scripted fake model against the real MCP server. Besides checking that good answers pass, tests feed deliberately bad answers to prove each evaluator fails when it should. Run with `uv run pytest agent`.
- **Live (never in CI):** benchmarks real Ollama models with the same cases and the same evaluators. It measures time to first token, total latency and tokens/s, and prints the failing answers.

```bash
cd spine_monitor/agent
SPINE_AGENT_EVALS_LIVE=1 uv run --no-sync python -m evals.run_live \
    --models qwen3.5:4b gemma4:e4b qwen3.5:9b --repeat 1 --dump failures.jsonl
```

## Model note

Measured on 2026-11-06 with Ollama 0.40.14 **in Docker, CPU only** (Intel Core Ultra 7 155H, 32 GB RAM, Docker VM with 16 GB; no discrete GPU), 28 cases, **one repetition**.

| Model | Size (Q4_K_M) | Pass rate | Time to first token (median / p90) | Total latency (median) | Tokens/s |
|---|---|---|---|---|---|
| **`qwen3.5:4b`** (chosen) | 3.3 GB | **82 %** (23/28) | 24.0 s / 67.4 s | 29.9 s | 2.1 |
| `gemma4:e4b` | 6.6 GB | 50 % (14/28) | 0.8 s / 51.5 s | 3.6 s | 6.8 |
| `qwen3.5:9b` | 6.6 GB | 68 % (19/28) | 40.0 s / 118.2 s | 51.6 s | 1.3 |

Tokens/s is output tokens over the time spent inside the model stream, so it includes prompt processing; that is why it is low on CPU.

**Why `qwen3.5:4b`:** it has the best pass rate and handles the refusals well (diagnosis traps 6/6, write requests 4/4). `gemma4:e4b` is the fastest, but its 0.8 s is misleading: in 10 of its 14 failed runs it did not call any tool and answered without reading data, which this agent must never do. `qwen3.5:9b` is slower and did worse here (simulation 0/4).

### Limits

- **Latency on CPU.** The chosen model needs a median of 24 s (p90 67 s) to start answering on this laptop. The 15 s target we set was not met by any model that also read the data. A discrete NVIDIA GPU or an Apple Silicon Mac should be much faster, but that was not measured. Ollama in Docker on Windows does not use the Intel iGPU.
- **Small sample.** One repetition of 28 cases; results change between runs (the same model went from 57 % to 82 % after we fixed our own evaluators). Treat the numbers as an order of magnitude.
- **A 4B model sometimes fails.** In the failed runs it skipped a tool it should have called (one Catalan question, one Spanish simulation) or presented a scenario without running it. The fallback only covers an unavailable model, not a model that answers badly.
- **Simulations.** `simulate_scenario` requires at least one override, so "what if I keep this load?" (no change) cannot be expressed and the model usually asks the user for one. This is a limit of the MCP tool.
- **No current date.** The prompt does not include the date, so questions such as "how has my status changed this week?" cannot be answered (`get_history` needs absolute dates).
- **Keyword-based checks.** The diagnosis post-filter and several evaluators use word lists in English, Spanish and Catalan: they miss phrasings not in the lists, and an assertion can only be corrected after it was streamed.
- **Catalan is weaker** than English and Spanish in the small models (one run read "Mart" as a time of day).
- **No amber from the real engine yet.** The engine's triage is still a stub that always returns green, so "why am I amber?" only works with the mock data.
