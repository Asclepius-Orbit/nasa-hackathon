**English** · [Español](README.es.md) · [Català](README.ca.md)

# nasa-hackathon

## Development environment (Python)

`spine_monitor/` is a uv workspace: a single `.venv` and a single `uv.lock` for every package. Always work from `spine_monitor/` (`uv sync --all-packages`, `uv run pytest`). In PyCharm, open the `spine_monitor/` folder and pick its `.venv` as the interpreter.

## Claude Code skills for pull requests

Two project skills in `.claude/skills/` drive PR review with subagents:

- `review-pr`: reviews a PR. It reads the PR, linked issue, CI and threads first, runs parallel reviewers (correctness, tests/TDD, security), verifies each blocker, and publishes one review with `gh`: request changes with inline suggestions if there are blockers, approve if there are only style nits. On your own PR, GitHub forbids a formal verdict, so it posts a `COMMENT` review whose first line is the verdict.
- `receive-review`: applies the requested changes on a PR. It triages each comment against the code, rebases onto `develop`, applies fixes with TDD, fixes failing CI, re-runs the quality gate, pushes with `--force-with-lease`, resolves threads and re-requests review.

Both ask for confirmation before publishing, committing or pushing, and never add Claude attribution.
