# Template: CI diagnosis subagent (receive-review, Phase 4)

Fill in the `<...>` markers and pass it as the prompt of a subagent (`general-purpose`, medium Sonnet by default, high Opus if the failure is about concurrency or security). One subagent per failed job. Independent failures in parallel; if they touch the same files, in sequence.

```
Diagnose and fix a failed CI job on PR #<n> (repo Asclepius-Orbit/nasa-hackathon), branch <branch>, commit <headRefOid>. Do not create branches, do not run git commit, git push or git rebase; the orchestrator does that.

## Failure

- Job: <job / check name>
- Run: <run-id>
- Log: get the log with `gh run view <run-id> --log-failed` and read it in full (not just the last line).

## Mandatory method (superpowers:systematic-debugging)

Load and follow the `superpowers:systematic-debugging` skill. Root cause BEFORE fixing:
1. Read the full error: message, trace, file:line, the command that failed.
2. Reproduce it locally with the fast loop (`cd spine_monitor && uv run pytest <path> -q -p no:randomly`, or ruff/pyright depending on the failed step). Do NOT launch the Docker gate (`GATE_MUTATION=changed docker compose ...`): the orchestrator runs it in Phase 5. If the failed step is `Diff coverage` or it only reproduces with Docker, return BLOCKED with the exact command for the orchestrator to run. If it does not reproduce, say so and do not guess.
3. Compare with what changed on the branch and formulate ONE hypothesis; test it with the minimal change. If it fails, go back to investigating; do not pile up fixes.
4. Fix the cause, not the symptom.

## Rules

- If the fix changes production code: follow `tdd-workflow`. Regression test seen RED, then the change, then GREEN. Return both outputs.
- Exempt (config/infra without logic, docs): verify them by running them.
- Forbidden: lowering thresholds (coverage, mutation, diff coverage), `# pragma: no mutate`, `# type: ignore`, disabling or skipping tests or checks, relaxing the workflow so it passes.
- `gh run rerun --failed` only if you prove it is flaky (the same commit passes and fails, external cause, with the evidence). Never as a substitute for diagnosis.
- If the cause is outside your scope (infra, secrets, external service) or the fix requires touching files I have not assigned to you: stop and return BLOCKED with evidence.
- Cap of 2 failed fix attempts: if the second fails, stop and return what you know.

## Assigned files

<List of files you may touch.>

## What to return

1. Status: FIXED / DEMONSTRATED FLAKY / BLOCKED.
2. Root cause, with `file:line` and the log line that proves it.
3. Local reproduction: command and literal output of the failure.
4. If there is a code change: literal output in RED (regression test) and in GREEN; file(s) touched and `git diff --stat`.
5. How you checked that the specific job now passes locally.
6. Risks or related failures you see.
Do not write commit messages with Co-Authored-By or "generated with Claude". If you refer to the maintainer, use the role ("the maintainer"), not a personal name.
```
