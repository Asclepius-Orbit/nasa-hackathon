---
name: mcp-tool-change
description: Use when adding, renaming or changing the parameters, output or errors of an MCP tool in `spine_monitor/mcp_server`, before touching `server.py`.
---

# mcp-tool-change

Changing a tool means changing a public contract read by a small local LLM. Every code change follows `tdd-workflow` (visible red first). For the tool's design (name, description, pagination, actionable errors) read `mcp-builder`; if it clashes with this repo, the repo rules.

Files (from `spine_monitor/mcp_server/`): `src/spine_mcp_server/{server,ports,mocks,compare,errors}.py`, `tests/{test_server,test_ports_mocks,test_compare,test_stdio}.py`, `README.md`, `README.es.md`, `README.ca.md`.

## Steps

1. **Decide the tool.** Name `verb_object` in snake_case and English, like `get_history`. Parameters in English, each with `Field(description=...)`; ids with `AstronautId`; windows with `AwareDatetime` and `_window(...)`. If it cannot be read-only or simulation, STOP and ask the maintainer: the server never writes data or diagnoses.
2. **Annotations and description.** `annotations=_READ_ONLY`. The description says what it returns, that data may be `null` or missing, and that it never diagnoses (a test requires it).
3. **Output: contract first.** If the tool returns a new or changed schema from `contracts/schemas/v1/`, use the `contract-first` skill (schema, example in `contracts/examples/valid/`, regenerate models) before continuing. If it reuses an existing one, do not touch it.
4. **Port.** If it needs new data, add the method to the Protocol in `ports.py` (`AppReader`, `Simulator` or `AssessBaseSource`) with a docstring and read-only. If the tool can be computed with existing ports, do so (like `compare_periods`, which calls `get_history` twice) and put the pure logic in a module with no I/O.
5. **Mock.** Implement the method in `mocks.py` serving the valid example (`EXAMPLES_DIR`); filter, sort from oldest to newest and compute `truncated` if there is a window and `limit`. Errors such as `UpstreamError(404, ...)` or `UpstreamError(422, "invalid_range", ...)`.
6. **Limits.** Lists with `limit` (1 to 500, default 100, `MAX_HISTORY_LIMIT`), `truncated` and the oldest discarded. No unbounded dumps.
7. **Errors.** Call the port inside `with _upstream_as_tool_errors():`; an `UpstreamError` comes out as `isError: true` with `title: detail`. A failure of our own logic raises `ToolError(str(error))`. Do not let other exceptions escape.
8. **Registration.** `@server.tool(name=..., annotations=_READ_ONLY, description=...)` in `create_server`; return `Annotated[CallToolResult, Model]` and `_result(model)` (use `dump`, not `model_dump`).
9. **Count.** If the number of tools changes, search for `7` and `seven` and fix them: docstrings in `server.py`, `tests/test_server.py` (`TOOLS`, `test_lists_exactly_the_seven_tools`, `test_main_serves_the_seven_tools_over_stdio`) and `tests/test_stdio.py`, and the opening sentence of the three READMEs ("7 read-only and simulation tools"...).
10. **READMEs.** In `README.md`, `README.es.md` and `README.ca.md`: the tools table row (parameters and output schema), the tool's notes and the count. All three in sync, with the language-switch link on the first line intact. No references to the Obsidian vault. In any text addressed to the team, refer to the user by role ("the maintainer").

## Tests (red first)

In-memory pattern from `tests/test_server.py`: `_server(app, simulator, base)` builds `create_server` with hand-written doubles (`RecordingApp` records the calls; `FailingApp` raises `UpstreamError(503, "Boom", ...)`); `_call`/`_tools` use `Client(server)` inside `@pytest.mark.anyio`. Write before the code:

- the tool appears in `TOOLS` and meets the cross-cutting tests (read-only, described parameters, output schema);
- a row in `CALLS` (marker `contract`): the output validates with `validate("<schema>", ...)`;
- the port receives the expected arguments and the default `limit` and its bounds;
- a port failure gives `is_error` with `Boom: it broke`; edge cases (empty, `null`, truncated);
- the mock in `tests/test_ports_mocks.py` (404, 422, order, `truncated`).

One marker per test (`unit`, `integration` or `contract`). `tdd-workflow` gate at the end.

## Manual verification (MCP Inspector)

From `spine_monitor/`, bound to localhost:

```bash
pnpm dlx @modelcontextprotocol/inspector@2.14.0 uv run spine-mcp-server
```

Check that the tool appears, its input and output schema, a valid call and an invalid one (`isError`).

## Warning signs — STOP

| Thought | Reality |
|---|---|
| "I'll add a tool that saves or corrects data" | The server is read-only and simulation. Ask the maintainer. |
| "The description can say 'diagnoses'/'detects injury'" | Describe evidence and risk levels, never diagnosis. |
| "I'll return 0 if the data is missing" | Missing = `null`, never 0. |
| "I'll edit the JSON Schema or `models/` by hand" | Use `contract-first`; models are regenerated. |
| "I'll call the app API from `server.py`" | Only through a port in `ports.py`. |
| "I'll return all the records" | `limit` 1 to 500 and `truncated`. |
| "I'll update only the English README" | All three READMEs at once, and the tool count. |
| "I'll test with the Inspector and skip the test" | Without a test red first there is no tool. |
