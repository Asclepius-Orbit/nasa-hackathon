**English** · [Español](README.es.md) · [Català](README.ca.md)

# nasa-hackathon

## Development environment (Python)

`spine_monitor/` is a uv workspace: a single `.venv` and a single `uv.lock` for every package. Always work from `spine_monitor/` (`uv sync --all-packages`, `uv run pytest`). In PyCharm, open the `spine_monitor/` folder and pick its `.venv` as the interpreter.

## Claude Code skills for pull requests

Two project skills in `.claude/skills/` drive PR review with subagents:

- `review-pr`: reviews a PR. It reads the PR, linked issue, CI and threads first, runs parallel reviewers (correctness, tests/TDD, security), verifies each blocker, and publishes one review with `gh`: request changes with inline suggestions if there are blockers, approve if there are only style nits. On your own PR, GitHub forbids a formal verdict, so it posts a `COMMENT` review whose first line is the verdict.
- `receive-review`: applies the requested changes on a PR. It triages each comment against the code, rebases onto `develop`, applies fixes with TDD, fixes failing CI, re-runs the quality gate, pushes with `--force-with-lease`, resolves threads and re-requests review.

Both ask for confirmation before publishing, committing or pushing, and never add Claude attribution.

## Third-party skills and tooling

Vendored skills in `.claude/skills/` (provenance and licences in `.claude/skills/THIRD_PARTY.md`):

- `mcp-builder`: designing MCP servers and their evals (for `mcp_server`).
- `property-based-testing`: Hypothesis properties for `physiology` and `engine`.
- `modern-python`: uv and ruff conventions. Its advice to use `ty` does not apply: this repo type-checks with pyright.
- `async-python-patterns`, `python-testing-patterns`, `python-type-safety`: asyncio, pytest and typing references.

The project `.mcp.json` registers Context7 (pinned version) so Claude Code can look up current docs for FastAPI, aiomqtt, the `mcp` SDK, numpy and pydantic. It sends the queries to Upstash.

Project skills for the domain: `physiology-engine`, `contract-first`, `agent-evals`, `mqtt-dev` and `mcp-tool-change`.

`.claude/skills/ROUTING.md` maps each kind of task to the skills that apply. `implement-feature`, `tdd-workflow`, `review-pr` and `receive-review` use it to name those skills in the subagent prompts.

Node tooling uses pnpm only (`pnpm install`, `pnpm run`, `pnpm dlx`); never npm or npx. The lockfile is `app/ui/pnpm-lock.yaml`.

New dependencies: `pint` (units, in `physiology`), `pydantic-evals` (dev, for agent evals), and `vitest` and `uplot` (dev, in `app/ui`).
