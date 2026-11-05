**English** · [Español](README.es.md) · [Català](README.ca.md)

# spine-mcp-server

## Description

MCP server (stdio) that exposes 7 read-only and simulation tools to the local LLM agent. It never writes data, never touches the app database and never diagnoses. Today it runs on mocks built from the contract examples (`spine_monitor/contracts/examples/valid/`), behind three Protocols (`AppReader`, `Simulator` and `AssessBaseSource` in `ports.py`) so the real HTTP clients can replace them later.

## Run

From `spine_monitor/`:

```bash
uv run spine-mcp-server          # or: uv run python -m spine_mcp_server
uv run pytest mcp_server         # tests
```

### Debug with the MCP Inspector

Interactive UI to call the tools and read their schemas. It runs on demand through `npx` and is not a project dependency. Keep it bound to localhost.

```bash
npx @modelcontextprotocol/inspector@2.14.0 uv run spine-mcp-server
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
- `overrides` is a list of changes with `feature_id`, `mode` (`set`, `add` or `scale`) and `value`. `project_days` is 1 to 365.
- `simulate_scenario` is hypothetical only and never changes real data.

## Errors

A failure from the app or the engine (for example 404 not found or 422 invalid range) becomes an MCP tool error (`isError: true`) with the message `title: detail`.

## compare_periods notes

- Features are compared per (`feature_id`, `protocol_id`, `gravity_environment`). A key with more than one unit is left out and flagged with `unit_mismatch`.
- Feature sets without protocol or gravity are ignored in the comparison but counted in `feature_sets_count`.
- If two assessments share `as_of`, `latest` is the worst level.
- Missing data is `null`, never 0.
- `warnings` flags empty periods (`period_a_empty`, `period_b_empty`) and truncated periods (`period_a_truncated`, `period_b_truncated`).

## Out of scope for now

- Real HTTP clients, waiting for the app API (issue #17).
