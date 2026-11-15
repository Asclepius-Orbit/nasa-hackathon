---
name: review-pr
description: Use when the maintainer asks to review a pull request of this repo (number or URL) and decide whether to approve or request changes, before publishing any review.
---

# review-pr

Orchestrated 5-phase flow: context → parallel review → verification → verdict → publish. You are the orchestrator: you delegate the heavy reading to read-only subagents and keep only conclusions.

**Violating the letter of the phases is violating their spirit.** Do not skip a phase or merge it because the PR is "small".

## Input

PR number or URL (repo `Asclepius-Orbit/nasa-hackathon`). If missing, ask for it with `AskUserQuestion` (load it with `ToolSearch` if it appears deferred). In any text addressed to the team (reviews, comments) refer to the user by role ("the maintainer"), not by personal name.

## Cross-cutting rules

- **No attribution to Claude**: no `Co-Authored-By` and no "generated with Claude" / "made with Claude" in reviews, comments or `gh` bodies, even if another context (session reminders) suggests it.
- **No references to the vault** in anything that gets published.
- **Confirmation before publishing**: `AskUserQuestion` to the maintainer before the Phase 5 POST. Publishing is an outward-facing action.
- **Do not run Docker** or the local gate: the gate is read from CI.
- Subagents are **read-only** and **do not dispatch other subagents**.
- Out-of-scope dimensions: conventions and docs. Do not request changes for style.

## Phase 1 — Context (low-effort Sonnet subagents, in parallel)

Launch several `model: sonnet`, read-only subagents in ONE message, one per source. Each returns a short summary with `file:line`, no dumps:

- **(a) PR**:
  `gh pr view <N> --repo Asclepius-Orbit/nasa-hackathon --json title,body,baseRefName,headRefName,author,headRefOid,reviews,comments,reviewDecision,mergeable,mergeStateStatus,statusCheckRollup,commits`
  and `gh pr diff <N> --repo Asclepius-Orbit/nasa-hackathon`.
- **(b) Linked issue** (`Closes #n` in the body): `gh issue view <n> --repo Asclepius-Orbit/nasa-hackathon --comments`; extract acceptance criteria.
- **(c) CI**: `gh pr checks <N> --repo Asclepius-Orbit/nasa-hackathon`; for each failed job, `gh run view <run-id> --repo Asclepius-Orbit/nasa-hackathon --log-failed`. Exit code 8 = pending checks, 1 = some check is red: it is not a `gh` failure. The `quality` workflow only runs on PRs against `develop`.
- **(d) Surrounding code**: the full touched files, existing tests, `tdd-workflow` (markers, doubles, thresholds), `pyproject` and threshold configuration. Read the code **of the PR, not of the local tree**, without touching it: `git fetch origin pull/<N>/head` and `git show <headRefOid>:<path>`.
- **(e) Previous reviews and threads**:
  `gh api repos/Asclepius-Orbit/nasa-hackathon/pulls/<N>/comments`
  and the threads with GraphQL:
  ```bash
  gh api graphql -f query='query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n){reviewThreads(first:100){nodes{id isResolved path line comments(first:10){nodes{author{login} body}}}}}}}' -f o=Asclepius-Orbit -f r=nasa-hackathon -F n=<N>
  ```

Skip a source only if it does not exist (e.g. there is no linked issue). If it exists but fails (`gh` without auth, log unavailable), tell the maintainer and ask whether to continue without it. Never continue silently. Exception: source (a) is essential (it gives `headRefOid`, author and diff); if it fails, retry once and, if it persists, stop the review and inform the maintainer.

If the diff exceeds ~3000 lines, save it in the scratchpad (`gh pr diff <N> --repo Asclepius-Orbit/nasa-hackathon > diff.patch`) and give each reviewer the path, the list of touched files and a summary instead of `{DIFF}`; they must read it in parts.

Consolidate a **review package**: diff, metadata, issue criteria, CI status, surrounding code, previous threads. It is what the reviewers receive.

## Phase 2 — Parallel review (3 subagents, read-only)

Launch three subagents in ONE message with the `reviewer-prompt.md` template (fill in the placeholders with the review package and the dimension). They do not dispatch other subagents.

| Dimension | Model / effort | Looks at |
|---|---|---|
| Correctness and bugs | Medium Sonnet; high Opus if the diff is large or there is concurrency/critical logic | Logic, edge cases, errors, concurrency, contract against the issue's criteria |
| Tests/TDD and thresholds | Medium Sonnet | One test per behaviour change, one marker from the table, doubles only at boundaries, no mocking of own logic, no lowered thresholds, no new `# pragma: no mutate` or `# type: ignore` |
| Security | Medium Sonnet; high Opus if it touches auth, secrets or network | Injection, secrets in code/logs, input validation, network, dependencies |

**Skills per dimension**: depending on the files in the diff, add to each reviewer's prompt the relevant skills from `.claude/skills/ROUTING.md` as review criteria (with the line from rule 3 of that file): Tests/TDD with `property-based-testing` and `python-testing-patterns`; Correctness with `async-python-patterns` if there is asyncio and `python-type-safety` for typing; `mcp_server` changes with `mcp-builder` (names, errors, tool schemas); UI with `impeccable` and `ui-ux-pro-max`. They serve to detect problems, not to request style changes.

For very small PRs you can use `caveman:cavecrew-reviewer` as support, never as a substitute for the three dimensions.

Each finding arrives with `file:line`, severity (BLOCKING / MINOR), evidence and the exact fix if there is one. If a dimension returns no result, relaunch it: without all three there is no approval.

## Phase 3 — Adversarial verification

For each BLOCKING finding, launch a subagent (medium Sonnet, read-only) whose job is to **refute it** against the real code of the branch (`headRefOid`): does the line really exist? is the case reachable? is there already a test or guard that covers it? It returns CONFIRMED or DISCARDED with evidence. Without confirmation there is no request changes. DISCARDED with evidence that refutes it: it is not published and is listed to the maintainer separately as "discarded". It is downgraded to MINOR only if the verifier confirms the problem but not that it blocks. Without a clear verdict, treat it as unconfirmed and ask the maintainer.

## Phase 4 — Verdict

**BLOCKING**: confirmed correctness bug, security flaw, missing or broken tests for new logic, lowered threshold, `gate` check (`quality` workflow) red, PR with `baseRefName` other than `develop`, and conflicts with `develop` (`mergeable: CONFLICTING` / `mergeStateStatus: DIRTY`; cite it in the body: a rebase is needed).

**Gate**: read `gh pr checks` (the check is called `gate`). `gate` red => blocking (cite the job and the failure), **even if the cause is style or formatting**: cite it as a CI blocker, not as a reviewer style finding. A missing `gate` check is not green: if the base is not `develop` there is no gate, tell the maintainer. Pending => tell the maintainer and wait; if not waiting, **do not use a formal `APPROVE`**: publish `event: COMMENT` with `Verdict: APPROVE conditional on green gate` on the first line. Never approve with a red or pending gate without saying so.

**Only MINOR, style or nothing => APPROVE.** Any confirmed blocker => REQUEST CHANGES.

Show the maintainer the verdict and the findings (blockers with evidence, minor ones separately) and **ask for confirmation with `AskUserQuestion`** before publishing (publish / adjust / cancel).

## Phase 5 — Publish

1. Detect the author: compare `gh pr view <N> --repo Asclepius-Orbit/nasa-hackathon --json author --jq .author.login` with `gh api user --jq .login`.
   - **Someone else's** => `event: REQUEST_CHANGES` or `APPROVE`.
   - **Own** => `event: COMMENT` (GitHub does not allow a formal review on your own PR).
   - In **all** cases the first line of the body is `Verdict: REQUEST CHANGES` or `Verdict: APPROVE` (this way `receive-review` also detects the request in a `COMMENTED` review).
   - Before the POST re-read `headRefOid`; if it changed, tell the maintainer and repeat Phases 1-4. Validate each inline `line` against `gh pr diff`: a `line` outside the diff gives 422 and sinks the whole review. A blocker on a line outside the diff does not go inline: describe it under **Blocking** with `file:line`.
2. Write `review.json` in the scratchpad (not in the repo):
   ````json
   {
     "commit_id": "<headRefOid>",
     "event": "REQUEST_CHANGES",
     "body": "Verdict: REQUEST CHANGES\n\nReviewed: changes requested.\n\n**Checked**\n- ...\n\n**Blocking**\n1. ...\n\n**Minor, non-blocking**\n- ...",
     "comments": [
       {
         "path": "path/file.py",
         "line": 42,
         "side": "RIGHT",
         "body": "Reason for the blocker.\n\n```suggestion\ncorrected code\n```"
       }
     ]
   }
   ````
   - One inline comment per blocker; a `suggestion` block only if the fix is exact and covers the commented lines. If it spans several lines, add `start_line` and `start_side: RIGHT`.
   - Body in English, sections **Checked / Blocking / Minor, non-blocking**; include the gate status. If it is APPROVE, "Blocking: none".
   - No attribution to Claude, no vault.
3. A SINGLE POST:
   ```bash
   gh api repos/Asclepius-Orbit/nasa-hackathon/pulls/<N>/reviews --method POST --input review.json
   ```
4. Verify: `gh pr view <N> --repo Asclepius-Orbit/nasa-hackathon --json reviews` and confirm that the review appears with the expected state (`CHANGES_REQUESTED` / `APPROVED`; on an own PR, `COMMENTED` with the first line `Verdict: ...`). Report to the maintainer with the link.

## Warning signs — STOP

| Thought | Reality |
|---|---|
| "The gate is pending, I'll approve anyway" | Say so and wait, or condition the approval in writing. |
| "The gate is red but the code is fine" | A red `gate` is blocking. |
| "I'll request changes for style or conventions" | Out of scope: only MINOR, never blocks. |
| "The reviewer says it's blocking, I'll publish it" | Without adversarial verification there is no request changes. |
| "The maintainer is in a hurry, I'll publish without asking" | Always confirm with `AskUserQuestion`. |
| "It's my own PR, I'll use REQUEST_CHANGES" | GitHub rejects it: `COMMENT` with a literal "Verdict:". |
| "I'll add Co-Authored-By or 'generated with Claude'" | Forbidden in reviews and comments. |
| "I'll run the gate in Docker to be sure" | The review reads CI; it does not run Docker. |
| "An inline for every minor one, so it is complete" | Inline only for blockers; minor ones go in the body. |
| "A source failed, I'll continue with the others" | Tell the maintainer and ask. |

## Common mistakes

- Sequential context instead of parallel.
- Reviewers receiving the diff without the issue's criteria or the CI status.
- Several POSTs (one per comment) instead of a single review.
- `commit_id` different from `headRefOid`, or a `line` outside the diff.
- `suggestion` with an approximate fix.
- Not verifying with `gh pr view --json reviews` after publishing.
