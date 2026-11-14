**English** · [Español](README.es.md) · [Català](README.ca.md)

# spine-mcp-server

## Description

MCP server (stdio) that exposes 8 read-only and simulation tools to the local LLM agent. It never writes data, never touches the app database and never diagnoses. By default (`--dataset http`) it reads from the real app read API and simulates through the real engine; `--dataset demo` and `--dataset examples` run on generated data and on the contract examples (`spine_monitor/contracts/examples/valid/`). In every case the data comes in behind three Protocols (`AppReader`, `Simulator` and `AssessBaseSource` in `ports.py`), so the source can be swapped.

## Run

From `spine_monitor/`:

```bash
uv run spine-mcp-server          # or: uv run python -m spine_mcp_server
uv run pytest mcp_server         # tests
```

### Debug with the MCP Inspector

Interactive UI to call the tools and read their schemas. It runs on demand through `pnpm dlx` and is not a project dependency. Keep it bound to localhost.

```bash
pnpm dlx @modelcontextprotocol/inspector@2.14.0 uv run spine-mcp-server
```

## Tools

| Tool | Parameters | Returns (contract schema) |
|---|---|---|
| `get_current_status` | `astronaut_id` | `app/status` |
| `get_history` | `astronaut_id`, `start`, `end`, `limit=100` | `app/history` |
| `get_sessions` | `astronaut_id`, `start`, `end`, `limit=100` | `app/session-list` |
| `get_self_reports` | `astronaut_id`, `start`, `end`, `limit=100` | `app/self-report-list` |
| `compare_periods` | `astronaut_id`, `a_start`, `a_end`, `b_start`, `b_end` | `mcp/compare-periods` |
| `explain_assessment` | `assessment_id` | `app/explanation` |
| `simulate_scenario` | `astronaut_id`, `overrides`, `project_days=None` | `engine/simulate-response` |

- `start` and `end` are RFC 3339 timestamps with a UTC offset. `limit` is 1 to 500.
- `compare_periods` reads both periods with `limit` 500 and computes the comparison in the server. Deltas are B minus A.
- `overrides` is an optional list (default empty) of changes with `feature_id`, `mode` (`set`, `add` or `scale`) and `value`; empty or omitted means no changes, so the current trajectory is projected as it is. `project_days` is 1 to 365.
- `simulate_scenario` is hypothetical only and never changes real data. In `--dataset http` its base is the assessment request the app would send to the engine now (`GET /crew/{astronaut_id}/assess-base`, `as_of` = the app's clock, last 14 days of data); if the app cannot build it (no profile, no baseline or no feature sets in the window) the tool returns a 404 error naming the reason. In particular, an astronaut with no feature sets in the last 14 days gets a 404 `no_feature_sets`.

## Datasets

`python -m spine_mcp_server` reads the real app and engine by default (`--dataset http`). The options are:

### Real app and engine (default)

`--dataset http` calls the app read API at `SPINE_APP_URL` (default `http://127.0.0.1:8002`) and the simulator calls the engine (`/simulate`) directly at `SPINE_ENGINE_URL` (default `http://127.0.0.1:8001`). The assessment base comes from the app read API (`/crew/{astronaut_id}/assess-base`). The engine runs the default pipeline with a declarative triage policy that is still provisional (its clinical catalog is pending review).

```bash
uv run python -m spine_mcp_server                                    # same as --dataset http
SPINE_APP_URL=http://127.0.0.1:8002 uv run python -m spine_mcp_server --dataset http
```

### Demo dataset (option)

`--dataset demo` serves a **demo dataset**: the last 14 days of astronaut `AST-01`, generated relative to the clock so the data always ends "now". It is green for the first 8 days and amber for the last 6, with a rising risk score (0.18 to 0.62), one assessment per day, an exercise session every 2 days plus a few spine checks, a daily self-report (pain NRS from 2 to 5) and one weekly ODI report. `explain_assessment` works for every assessment in that history, `compare_periods` has data in both weeks, and `simulate_scenario` answers what-if questions with a simple linear model on lumbar elongation (an approximation, not the engine). `AST-01` is the only astronaut; any other id returns a "not found" tool error. Its clock is wall time unless `--now` pins it.

### Options

| Option | Meaning |
|---|---|
| `--dataset http\|demo\|examples` | `http` (default): the real app read API at `SPINE_APP_URL` and the real engine at `SPINE_ENGINE_URL`; `demo`: the generated 14-day dataset of `AST-01`; `examples`: the single static snapshot of `contracts/examples/valid/` that the contract tests use |
| `SPINE_APP_URL` | Base URL of the app read API used by `http` (default `http://127.0.0.1:8002`) |
| `SPINE_ENGINE_URL` | Base URL of the engine used by the HTTP simulator (default `http://127.0.0.1:8001`) |
| `--now 2026-11-13T12:00:00Z` | Pins the demo clock (ISO 8601 with timezone, rounded down to the hour) for reproducible demos and tests; only affects `--dataset demo`; without it the wall clock is used |

The demo data is generated deterministically: the same `--now` always yields the same payloads and ids.

## Errors

A failure from the app or the engine (for example 404 not found or 422 invalid range) becomes an MCP tool error (`isError: true`) with the message `title: detail`.

In `--dataset http`, failures from the app API map as follows:

| Upstream failure | MCP tool error |
|---|---|
| Problem details 4xx (for example 404 or 422) | `title: detail`, upstream status kept; without `detail`, the field errors (`pointer: message`) take its place; capped at 500 characters |
| Other non-success below 500 (4xx, 3xx), without a problem body | `App API request rejected`, upstream status kept |
| 5xx | `App API error: HTTP <status>`, upstream status kept; the upstream detail is not forwarded |
| Response body that does not match the contract | 502, `App API returned an invalid response` |
| No response within 10 seconds | 504, `App API timeout` |
| Connection failure | 503, `App API unavailable` |

The simulator applies the same mappings to engine errors, identifying the engine as the upstream service.

## compare_periods notes

- Features are compared per (`feature_id`, `protocol_id`, `gravity_environment`). A key with more than one unit is left out and flagged with `unit_mismatch`.
- Feature sets without protocol or gravity are ignored in the comparison but counted in `feature_sets_count`.
- If two assessments share `as_of`, `latest` is the worst level.
- Missing data is `null`, never 0.
- `warnings` flags empty periods (`period_a_empty`, `period_b_empty`) and truncated periods (`period_a_truncated`, `period_b_truncated`).


### `search_evidence`

Searches the bundled offline evidence index; returns source titles, URLs, page locators and limitations. No match means insufficient evidence. `SPINE_EVIDENCE_DIR` overrides the directory containing `corpus.json` and `index.json`; missing or stale resources return a tool error. Rebuild after editing the corpus from `spine_monitor/`: `uv run python -c "from pathlib import Path; from spine_mcp_server.evidence import build_index; p=Path('mcp_server/src/spine_mcp_server/evidence_data'); build_index(p/'corpus.json', p/'index.json')"`.
