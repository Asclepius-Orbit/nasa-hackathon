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
sh app/mosquitto/setup.sh   # once: broker credentials in app/.env
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
- Mutation testing does not run by default. With `GATE_MUTATION=changed` it is incremental: only the functions and methods whose lines changed in your committed work against `origin/develop` (or against `MUTATION_BASE` if set), and their aggregate mutation score must be >= 80 %. Changed lines outside any function (imports, constants, class attributes) add no mutants, because mutmut never mutates them. Minimum sample (a conscious decision by Uri): when `changed` scores fewer than 10 mutants (`MUTATION_MIN_SAMPLE`, 0 disables the rule), a score below 80 % is printed with the notice `score shown, not enforced` and the exit code is 0, because one flipped mutant moves the score by 100/N points and the measured noise is about 3 mutants per run; a change that selects fewer than 10 mutants can therefore pass without its tests killing them. Infrastructure errors still fail the gate.
- With `GATE_MUTATION=full` it covers every package, only reports and never fails; a full physiology run takes about 108 minutes measured on a 22-CPU machine, and is estimated to be an order of magnitude slower on a 2-vCPU hosted runner, longer than the 360-minute job maximum, so it is run locally with `GATE_MUTATION=full docker compose -f app/docker-compose.yml run --rm quality`. The `full-mutation` workflow is manual only (`workflow_dispatch`), for a self-hosted or larger runner or for a single package (input `packages`, forwarded as `MUTATION_PACKAGES`).
- Mutation runs (`changed` and `full`) do not mutate logging calls: the arguments of statements such as `_log.warning("...", x)` (message text, format arguments, `exc_info`) change no behaviour the tests observe, so `quality/mutation.sh` generates mutmut's `do_not_mutate_patterns` to skip exactly the statements that start a `debug`/`info`/`warning`/`warn`/`error`/`exception`/`critical`/`log` call on a `log`, `_log`, `logger` or `_logger` object. Nothing else is skipped and the production code carries no `# pragma: no mutate`. Today this removes 125 of the 8,993 physiology mutants, all in `gateway/app.py`.
- Markers: each test carries exactly one of `unit`, `integration`, `contract`, `property` or `golden` (`mosquitto` is additional). `conftest.py` defaults to `unit`, to `integration` for `mosquitto`-marked tests and to `contract` for tests under `contracts/tests`.
- To regenerate the golden snapshot `physiology/tests/golden/tiny-mission.json` deliberately, run the golden test with `UPDATE_GOLDEN=1` and review the diff.
- CI: every PR to `develop` runs the full gate with incremental mutation and requires diff coverage >= 90 % on the changed lines. Tests and incremental mutation run on separate runners; the `gate` check succeeds only if both succeed. The full mutation report is run manually (see above), not on a schedule. Each Python job has a 240-minute timeout.
