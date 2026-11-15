# Template: fixes subagent (receive-review, Phase 4)

Fill in the `<...>` markers and pass it as the prompt of a subagent (`general-purpose`, model and effort according to the approved plan). One subagent per independent group of changes.

```
You apply a group of changes requested in the review of PR #<n> (repo Asclepius-Orbit/nasa-hackathon), branch <branch>. You work in the current tree: do not create branches, do not run git commit, git push or git rebase; the orchestrator does that.

## Group of changes

<For each comment in the group: author, file:line, literal text of the comment, action agreed with the maintainer, and evidence from the verification if the comment was doubtful.>

## Files of the group

<List. You only touch these; if you need another, stop and say so.>

## Context

<Relevant conventions, existing tests (paths), thresholds, decisions already taken with the maintainer.>

## Mandatory method (tdd-workflow)

Every production code change follows this cycle:
1. Red: write the test that describes the requested behaviour (a single marker: unit, integration, contract, property or golden). Run it and CHECK that it fails for the right reason (an expected assertion or import, not syntax or fixture). Save the output.
2. Green: minimal change. Run and save the output.
3. Refactor only with the suite green.
4. Bugfix: first a regression test seen red.
Fast loop: `cd spine_monitor && uv run pytest <path> -q -p no:randomly`.

Exempt (only docs/README and config/infra without logic): verify them by running them and say so.
Test doubles only at boundaries (HTTP, MQTT, clock, randomness); hand-written fakes before mocks; never mock your own logic.
Forbidden: lowering thresholds, `# pragma: no mutate`, `# type: ignore` (only `# pyright: ignore[rule]` with a reason, and if you need it, stop and ask). READMEs: if you touch one, sync README.md, README.es.md and README.ca.md.

## When to stop and ask

If a comment turns out to be ambiguous, contradicts the code or the tests, or the fix requires going outside the group's files: do NOT improvise. Return status BLOCKED with the question and the evidence.

## What to return (everything, not just the summary)

1. Status: DONE / BLOCKED (with the question).
2. For each comment: what you changed and in which `file:line`.
3. Literal test output in RED (before the change) and in GREEN (after). Without a visible red, the orchestrator rejects the group.
4. List of files touched and `git diff --stat`.
5. Doubts or risks you see.
Do not write commit messages with Co-Authored-By or "generated with Claude". If you refer to the maintainer, use the role ("the maintainer"), not a personal name.
```
