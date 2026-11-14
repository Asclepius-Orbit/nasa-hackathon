**English** · [Español](README.es.md) · [Català](README.ca.md)

# spine-agent

Local LLM agent that answers questions about an astronaut's spine-health status using the tools of the MCP server (`spine_monitor/mcp_server`). It runs fully offline against a local [Ollama](https://ollama.com) model, streams the answer token by token, and answers in the language of the question (English, Spanish or Catalan).

Rules it follows, by design:

- **Read-only data.** It cannot change astronaut data or the traffic light. `POST /report` can return an extractive evidence report, but it does not modify astronaut data or send the report elsewhere.
- **It does not diagnose.** No medical diagnosis and no treatment advice; clinical questions go to the flight surgeon.
- **It says "I don't know"** when a tool returns no data or an error. It never invents numbers (a missing value is "not available", never 0), and the prompt tells it to quote the values the tools return instead of computing new ones.
- **If the LLM fails, nothing else does.** The agent is a separate process: the direct app-engine path does not depend on it. If Ollama is down, times out or answers nothing, the agent prints a deterministic summary built from the MCP tools instead (no LLM involved).

These rules are enforced in three layers: the system prompt, a code post-filter (`guards.py`) that appends a correction notice if an answer asserts a diagnosis, and the evals below.

## How it works

```
spine-agent (CLI, streaming chat)
  └─ pydantic-ai Agent ── OllamaModel ──► Ollama (OpenAI-compatible API, http://localhost:11434/v1)
        └─ MCP toolset (stdio) ──► python -m spine_mcp_server   (7 read-only tools)
  └─ if the model fails: McpStatusSource ──► get_current_status + explain_assessment ──► template
```

- Thinking mode is disabled (it breaks tool calling on Qwen 3.5 in Ollama), the temperature is 0.2 and Ollama runs with a 16k context (its default of 4k silently truncates the tool schemas).
- Every turn the instructions carry the current date and time (UTC) and three **ready-made ranges** computed by code (last 24 hours, last 7 days, previous 7 days). A small model is bad at date arithmetic, so it copies the ranges into `get_history` / `compare_periods` instead of computing dates.
- The prompt asks the model to call a tool before stating any fact about the astronaut ("tool first"). It is only a prompt rule: nothing in the code forces it.

## Quick start

Needs Docker. Ollama runs in Docker under the `llm` compose profile, and the models are **cached in the `ollama-models` volume**: they are downloaded once, and later starts need no network.

```bash
# From the repository root. Once: the broker credentials (compose needs them).
sh app/mosquitto/setup.sh
# Prepare models explicitly while online (the normal startup never downloads models).
docker compose -f app/docker-compose.yml --profile llm-prepare run --rm ollama-pull
# Start the LLM runtime and chat API (agent, 127.0.0.1:8003), including offline.
docker compose -f app/docker-compose.yml --profile llm up -d

# Chat (streaming, multi-turn). Exit with /exit, /quit, Ctrl-D or Ctrl-C.
cd spine_monitor
uv run spine-agent --astronaut AST-01
```

By default the agent launches the MCP server with `--dataset http`, which reads the real app read-api and engine, so both must be running (`SPINE_APP_URL` and `SPINE_ENGINE_URL` are forwarded to the MCP subprocess). `AST-01` is the demo astronaut of the MCP server (`--dataset demo`): its data are generated relative to the clock (14 days, amber for the last 6); to chat against it set `SPINE_AGENT_MCP_ARGS='["-m","spine_mcp_server","--dataset","demo"]'`. Try `Why am I amber?`, `How has my status changed this week?` or `What if I keep this load for 30 days?`. `--astronaut` is required. `--model` overrides the model.

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
| `SPINE_AGENT_MCP_COMMAND` / `SPINE_AGENT_MCP_ARGS` | this Python / `["-m","spine_mcp_server","--dataset","http"]` | How the MCP server is launched (args as JSON); the default reads the real app and engine (they must be running; `SPINE_APP_URL` and `SPINE_ENGINE_URL` are forwarded); use `"--dataset","demo"` for the clock-relative demo data (`AST-01`) or `"--dataset","examples"` for the static contract examples |
| `SPINE_AGENT_MCP_LOG_FILE` | none | Where the MCP server log goes; discarded by default so it does not mix with the chat |
| `SPINE_AGENT_HTTP_HOST` / `SPINE_AGENT_HTTP_PORT` | `127.0.0.1` / `8003` | Address of the chat HTTP server |
| `SPINE_AGENT_HTTP_TOKEN` | none | Bearer token of the chat HTTP server, shared with the Electron main process |
| `SPINE_AGENT_HTTP_TOKEN_FILE` | `~/.spine-agent/http-token` | Where the token is read from when `SPINE_AGENT_HTTP_TOKEN` is unset; created with a random token (owner-only) when missing |
| `SPINE_AGENT_HTTP_MAX_CONVERSATIONS` | `16` | Conversations kept in memory; when full, the least recently used idle one is forgotten |
| `SPINE_AGENT_HTTP_IDLE_TTL_S` | `3600` | An idle conversation is forgotten after this many seconds |
| `SPINE_AGENT_HTTP_KEEPALIVE_S` | `15` | A `: ping` SSE comment is sent after this many seconds without an event |

## Chat HTTP server (for the desktop app)

The desktop app talks to the agent over local HTTP with server-sent events. The contract is [`contracts/agent-openapi.yaml`](../contracts/agent-openapi.yaml) with the schemas `contracts/schemas/v1/agent/chat-request.schema.json` and `chat-event.schema.json`.

```bash
cd spine_monitor
SPINE_AGENT_HTTP_TOKEN=change-me uv run spine-agent-http     # http://127.0.0.1:8003

curl -N http://127.0.0.1:8003/chat \
  -H "Authorization: Bearer change-me" -H "X-Astronaut-Id: AST-01" -H "Content-Type: application/json" \
  -d '{"schema_version":"1.0.0","astronaut_id":"AST-01","conversation_id":"c0ffee00-0000-4000-8000-000000000001","question":"Why am I amber?"}'

curl -N http://127.0.0.1:8003/report \
  -H "Authorization: Bearer change-me" -H "X-Astronaut-Id: AST-01" -H "Content-Type: application/json" \
  -d '{"schema_version":"1.0.0","astronaut_id":"AST-01","conversation_id":"c0ffee00-0000-4000-8000-000000000002","question":"Report on microgravity seated height evidence and cite sources."}'
```

- `POST /chat` answers 200 with a `text/event-stream`. Each event is `event: <type>` plus one `data:` JSON line: `text` (`delta`), `tool_call` (`tool_name`, `tool_args`), `fallback` (`text`), `notice` (`code` `mid_stream` or `correction`, `text`), and exactly one `end` or `error` (`code` `busy`, `unavailable`, `invalid` or `internal`, `message`) last. `: ping` comments keep the connection alive while the model thinks (on CPU the first token takes about 27 s, sometimes a minute).
- `POST /report` accepts the same authenticated request and streams the same event format. It uses the model to search local evidence, then emits only complete retrieved passages with their title, URL, locator and limitations; model-written claims are withheld. With no usable passages it returns an explicit insufficient-evidence message. It does not add the report to chat history.
- The client generates `conversation_id`: a new id starts a conversation, the same id continues it. The history lives in memory only, bounded and with an idle TTL; a conversation belongs to the astronaut of its first question.
- One answer at a time per conversation (a second question meanwhile is a 409). To cancel an answer, close the connection: the model run stops and the turn is not kept. `DELETE /chat/{conversation_id}` forgets a conversation (204, also when unknown).
- Every request but `/health` needs `Authorization: Bearer <token>` and `X-Astronaut-Id` (the unlocked astronaut, equal to the body's `astronaut_id`): 401, 403, 422 and 503 are problem details with the `app/error` schema.
- Without the LLM (Ollama down or failing) the answer is still a stream: a `fallback` summary built from the MCP tools, then `end`. If the MCP server itself cannot start, the stream ends with `error` `unavailable`.
- The server starts the MCP server once (stdio subprocess) and stops it on shutdown. A Docker image is in `agent/Dockerfile` (it listens on `0.0.0.0:8003` inside the container and keeps the generated token in `/data`).

For an opt-in end-to-end offline check of `POST /report` with the real engine, read API, MCP server and local Ollama, see [`tests/offline-125.md`](../tests/offline-125.md). It runs on an isolated Docker network and is not part of the quality gate.

## Evals

- **Offline (part of the quality gate):** 32 cases (18 English, 7 Spanish, 7 Catalan) in `evals/dataset.py`, run with a scripted fake model against the real MCP server on the demo dataset with a fixed clock. Categories: amber explanation, no data, diagnosis traps, write requests, simulations, history trends and out-of-scope questions. The evaluators also check that numbers in the answer match a tool result (`NumbersGrounded`, sign-insensitive), that `get_history` and `compare_periods` use the ready-made ranges exactly (`RangesMatch`, on the history cases) and that in the compare-weeks case period A is this week (`ComparisonDirection`, arguments only). Besides checking that good answers pass, tests feed deliberately bad answers to prove each evaluator fails when it should. Run with `uv run pytest agent`.
- **Live (never in CI):** benchmarks real Ollama models with the same cases and the same evaluators. It measures time to first token, total latency and tokens/s, and prints the failing answers.

```bash
cd spine_monitor/agent
SPINE_AGENT_EVALS_LIVE=1 uv run --no-sync python -m evals.run_live \
    --models qwen3.5:4b gemma4:e4b qwen3.5:9b --repeat 1 --dump failures.jsonl
```

## Model note

Measured on 2026-11-06 with Ollama 0.40.14 **in Docker, CPU only** (Intel Core Ultra 7 155H, 32 GB RAM, Docker VM with 16 GB; no discrete GPU), **one repetition** per model.

| Model | Size (Q4_K_M) | Pass rate | Time to first token (median / p90) | Total latency (median) | Tokens/s | Cases |
|---|---|---|---|---|---|---|
| **`qwen3.5:4b`** (chosen) | 3.3 GB | **81 %** (26/32) | 27.2 s / 60.7 s | 30.4 s | 2.1 | 32, final prompt |
| `gemma4:e4b` | 6.6 GB | 50 % (14/28) | 0.8 s / 51.5 s | 3.6 s | 6.8 | 28, first prompt |
| `qwen3.5:9b` | 6.6 GB | 68 % (19/28) | 40.0 s / 118.2 s | 51.6 s | 1.3 | 28, first prompt |

Tokens/s is output tokens over the time spent inside the model stream, so it includes prompt processing; that is why it is low on CPU. **The three rows are not strictly comparable**: `gemma4:e4b` and `qwen3.5:9b` were measured first, with the first prompt and 28 cases, and were not repeated after the prompt, the date handling and the demo dataset changed (only the chosen model was re-measured). All rows were measured on the demo dataset; a run against the real engine is still pending.

**Why `qwen3.5:4b`:** it has the best pass rate and handles the refusals well (diagnosis traps 6/6, write requests 4/4). `gemma4:e4b` is the fastest, but its 0.8 s is misleading: in 10 of its 14 failed runs it did not call any tool and answered without reading data, which this agent must never do. `qwen3.5:9b` is slower and did worse (simulation 0/4).

**The 4B result moves a lot between runs.** The same model scored 82 % (first prompt, 28 cases), 91 % (tool-first prompt, current date, 32 cases) and 81 % (ready-made ranges and "do not compute numbers", 32 cases); expect roughly ±10 points with one repetition. In the last run, `simulation` (3/4, `overrides` empty works now), `amber_explanation` (5/5), `diagnosis_trap` (6/6), `write_request` (4/4) and `no_data` (5/5) were solid. `history_trend` was 0/4: two of those four were an evaluator that insisted on `get_history` when the model had correctly answered with `compare_periods` (fixed afterwards, so the table above still counts them as failures), and the other two were real: the model computed differences itself and, in "compare this week with the previous one", swapped which week was which.

### Limits

- **Latency on CPU.** The chosen model needs a median of 27 s (p90 61 s) to start answering on this laptop; history questions are the slowest because their tool results are big and must be processed on CPU. The 15 s target we set was not met by any model that also read the data. A discrete NVIDIA GPU or an Apple Silicon Mac should be much faster, but that was not measured. Ollama in Docker on Windows does not use the Intel iGPU.
- **Small sample.** One repetition of 32 cases; results change between runs by about ±10 points. Treat the numbers as an order of magnitude.
- **A 4B model sometimes fails.** It can skip a tool it should have called, present a simulation without labelling it hypothetical, compute numbers that no tool returned, or swap the two periods of a comparison. The fallback only covers an unavailable model, not a model that answers badly, and the "tool first" rule is not enforced by code.
- **Keyword-based checks.** The diagnosis post-filter and several evaluators use word lists in English, Spanish and Catalan: they miss phrasings not in the lists, and an assertion can only be corrected after it was streamed.
- **Catalan is weaker** than English and Spanish in the small models (one run read "Mart" as a time of day, another one mixed "improved" with a worsening).
- **Measured on demo data, not yet on the real engine.** The offline evals and the model table use the demo dataset (a fixed clock, and `simulate_scenario` there is a simple linear approximation). The engine now runs the default pipeline, but its triage policy is declarative and provisional (the clinical catalog is pending review), and the evals have not been re-run against it.
