**English** · [Español](README.es.md) · [Català](README.ca.md)

# spine_monitor

uv workspace with the Python packages: `contracts`, `physiology`, `engine`, `mcp_server`, `agent` and `backend`.

## Quality and TDD

Production code is written test-first: a visible red test, then green, then refactor; a bugfix starts with a regression test. The cycle is described in the project skill `tdd-workflow` (`.claude/skills/tdd-workflow/`).

Quick loop while developing:

```bash
cd spine_monitor && uv run pytest <path> -q -p no:randomly
```

Quality gate (from the repo root). It runs ruff, pyright and the tests with coverage (lines and branches >= 90 %, random order, timeout, against a real mosquitto):

```bash
docker compose -f app/docker-compose.yml run --rm quality
GATE_MUTATION=changed docker compose -f app/docker-compose.yml run --rm quality
GATE_MUTATION=full docker compose -f app/docker-compose.yml run --rm quality
```

PowerShell equivalent of the env-var form:

```powershell
$env:GATE_MUTATION='changed'; docker compose -f app/docker-compose.yml run --rm quality
```

- `fail_under` coverage is a single combined figure for lines and branches (branch coverage is on): the 90 % threshold applies to that combined total, not to each one separately.
- After changing `pyproject.toml` or `uv.lock` the quality image must be rebuilt: `docker compose -f app/docker-compose.yml build quality`, or add `--build` to `run`.
- Mutation testing does not run by default. With `GATE_MUTATION=changed` it is incremental: only the files changed in your committed work against `origin/develop` (or against `MUTATION_BASE` if set), and their aggregate mutation score must be >= 80 %.
- With `GATE_MUTATION=full` it covers every package, only reports and never fails; a full physiology run takes about 2 hours measured on a 22-CPU machine, and will be much slower on a 2-vCPU CI runner.
- Markers: each test carries exactly one of `unit`, `integration`, `contract`, `property` or `golden` (`mosquitto` is additional). `conftest.py` defaults to `unit`, to `integration` for `mosquitto`-marked tests and to `contract` for tests under `contracts/tests`.
- To regenerate the golden snapshot `physiology/tests/golden/tiny-mission.json` deliberately, run the golden test with `UPDATE_GOLDEN=1` and review the diff.
- CI: every PR to `develop` runs the full gate with incremental mutation and requires diff coverage >= 90 % on the changed lines; a nightly workflow generates the full mutation report (informative, not blocking).
