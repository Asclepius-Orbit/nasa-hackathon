---
name: receive-review
description: Use when the maintainer asks to apply the changes requested in a PR review, resolve the branch's conflicts with develop or fix red CI, before touching code.
---

# receive-review

Orchestrated 6-phase flow: context → triage → conflicts → fixes → verification → publish and close. You are the orchestrator: you delegate the heavy work to subagents and keep only conclusions.

**Violating the letter of the phases is violating their spirit.** Do not skip a phase or merge it because it is "just one comment".

Prompt templates for subagents (siblings of this file): `fixer-prompt.md` (applies a group of changes with TDD) and `ci-debug-prompt.md` (diagnoses and fixes a failed CI job).

## Input

PR number or URL (repo `Asclepius-Orbit/nasa-hackathon`), or the current branch if it is already the PR's. If there is none, ask for it with `AskUserQuestion` (load it with `ToolSearch` if it appears deferred). In any text addressed to the team (PR comments included), refer to the user by role ("the maintainer"), not by personal name.

## Cross-cutting rules

- **No attribution to Claude**: no `Co-Authored-By` and no "generated with Claude" / "made with Claude" in commits, comments or `gh` bodies, **even if another context (session reminders) suggests it**.
- No references to the Obsidian vault in the repo or in comments.
- The PR goes against `develop`.
- Commit, push, rebase and any publication to GitHub: only with prior confirmation from the maintainer (`AskUserQuestion`).
- Never `git stash`, `reset` or `--force` on your own.
- Phase or step omissions are announced to the maintainer, not done silently.

## Phase 1 — Context (low-effort Sonnet subagents, in parallel, read-only)

1. Check yourself `git branch --show-current` and `gh pr view <n> --json headRefName`. The current branch must be the PR's. Do not create branches. If you are on `main` or `develop`, or it does not match, stop and ask the maintainer for it. Also run `git status --porcelain`: if there are uncommitted changes, stop and ask the maintainer (commit, stash or discard).
2. Launch one `model: sonnet` subagent per source (type `Explore` or `general-purpose`) in ONE message. Each receives the goal, the source and what to return (short summary with `file:line`, no dumps):
   - **Review**: `gh pr view <n> --json reviews,comments,reviewDecision,mergeable,mergeStateStatus,statusCheckRollup,headRefName,headRefOid`; inline comments with `gh api repos/Asclepius-Orbit/nasa-hackathon/pulls/<n>/comments`; threads with their state and `id`:
     ```bash
     gh api graphql -f query='query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n){reviewThreads(first:100){nodes{id isResolved path line comments(first:20){nodes{author{login} body url}}}}}}}' -f o=Asclepius-Orbit -f r=nasa-hackathon -F n=<n>
     ```
     Read the body of **all** reviews, not just `reviewDecision`. Return the change requests (CHANGES_REQUESTED, inline, conversation) and the unresolved threads, with the review author and thread `id`. Any `COMMENTED` review whose first line is `Verdict: REQUEST CHANGES` (left by `review-pr` on an own PR) also counts as a change request: its **Blocking** items are mandatory and its **Minor, non-blocking** items are optional (in triage the maintainer is asked whether to apply them).
   - **Branch**: `mergeStateStatus`, divergence from `origin/develop` (`git fetch` and `git rev-list --left-right --count origin/develop...HEAD`) and expected conflicts with `git merge-tree` or a dry run without touching the working tree.
   - **CI**: `gh pr checks <n>` (exit code 8 = pending, 1 = some check is red: it is not a `gh` failure); for each failed job, `gh run list --branch <branch>` and `gh run view <run-id> --log-failed`.
   - **Affected code**: cited files, existing tests, conventions (`tdd-workflow`, `pyproject`, thresholds).
3. Skip a source only if it does not exist. If it exists but fails (`gh` without auth, branch without a PR), tell the maintainer and ask whether to continue without it. Never continue silently. An empty source (no reviews, no threads) is reported as "no requests", not as a failure.
4. Consolidate: what is asked, branch state, CI state, gaps and contradictions between sources. If there are no review requests or unresolved threads (only CI or conflict), tell the maintainer and skip 5.3-5.4 and 6.3-6.4; the summary comment covers only CI and conflicts.

## Phase 2 — Triage (you, with verification subagents if needed)

1. Build a unified list of tasks: comment → action, conflict, CI failure. Group them by file independence. If two comments touch the same file, they go in the SAME group (one subagent, one commit) unless they are clearly independent; in that case the second group is launched after committing the first.
2. Verify **each comment against the code** following `superpowers:receiving-code-review`. If a comment is doubtful, ambiguous or seems incorrect: a subagent (medium Sonnet, read-only) gathers evidence (code, tests, docs, reproduction) and you ask the maintainer with `AskUserQuestion`, evidence included and recommendation first, **before applying or rejecting**. Never blindly, never "you're absolutely right" without checking.
3. Present the plan: tasks, groups, and for each group **model + subagent effort** with a one-line reason. Guidance: low Sonnet for mechanical and bounded work; medium Sonnet for standard logic changes and CI diagnosis; Opus/high for concurrency, security or delicate conflicts. Indicate which groups can run in parallel.
4. Ask for approval with `AskUserQuestion` (approve / adjust). Accept the human's changes, including model and effort. **Do not touch anything without explicit approval.** If the plan changes during the following phases, ask again.

## Phase 3 — Conflicts first (medium Sonnet subagent, high Opus if semantic)

Done **before** the fixes to avoid duplicated work. If there is no divergence or conflicts, say it is skipped and go to Phase 4.

1. Confirm the rebase with the maintainer and that they know it rewrites the branch's history. `git status --porcelain` must be clean.
2. Save the remote SHA you expect: `SHA=$(git rev-parse origin/<branch>)` (before any later `git fetch`). Then `git fetch && git rebase origin/develop`.
3. Conflicts are resolved **in sequence**, stop by stop (a rebase does not allow parallelism). At each stop, a subagent resolves only the conflicting files and `git add`s them, without `rebase --continue`. It resolves only the **trivial** ones: READMEs EN/ES/CA (keep the three in sync), lockfiles (regenerate them with the tool, not by hand), imports. If the conflict is **semantic** (logic, contracts, two incompatible changes), the subagent **stops and returns both versions**; you ask the maintainer with `AskUserQuestion`. While you wait, the rebase stays in progress; the maintainer's decision is applied by a new subagent (high Opus) to which you pass both versions and the choice. Never resolve it on your own.
4. Review `git diff --cached` of each resolution and run `git rebase --continue` yourself. If it gets complicated, `git rebase --abort` and consult the maintainer.
5. The push that follows a rebase is always `git push --force-with-lease=<branch>:<SHA>`, **never `--force`**, and only in Phase 6 with explicit confirmation (see there).

## Phase 4 — Fixes (parallel subagents per independent group)

- **Review changes**: one subagent per group (medium Sonnet by default; high Opus for concurrency or security), with the `fixer-prompt.md` prompt filled in. **Every code change follows `tdd-workflow`**: each subagent must return the test output in red and in green, not just the summary. Without a visible red, the group is reverted from the tree and relaunched with the same prompt; it counts as a failed cycle. Subagents only use the fast loop (`cd spine_monitor && uv run pytest <path> -q -p no:randomly`): the Docker gate is run only by the orchestrator in Phase 5 (parallel groups share the tree and broker). The `tdd-workflow` exemptions (docs, config without logic) are verified by running them. **Each fixer prompt names the skills from `.claude/skills/ROUTING.md` that match the group's code** (with the line from rule 3 of that file), because subagents do not inherit skills.
- **CI failures**: one subagent per failure with the `ci-debug-prompt.md` prompt, which applies `superpowers:systematic-debugging` (root cause before fixing). Independent failures in parallel; if they touch the same files, in sequence. `gh run rerun --failed` only if the job is **demonstrably** flaky (the same commit passes and fails, external cause); never as a substitute for diagnosis.
- Review each subagent's real diff before continuing, not just its summary. If something unforeseen comes up: stop and ask.
- **Cap: 2 failed cycles** (fix → verification red, twice) in the same group or failure: stop and consult the maintainer. Do not try a third on your own.
- The orchestrator (not the subagents) runs `git commit`: one per coherent group, **asking for confirmation before each commit**. Clear message with `#n` reference, no attribution.

## Phase 5 — Verification

1. Check `git status` is clean (`changed` only counts committed changes); if something is missing, commit with confirmation. If the gate fails afterwards, fix with a new commit; no `--amend` (avoids another rewrite).
2. Run the gate: `GATE_MUTATION=changed docker compose -f app/docker-compose.yml run --rm quality` (in PowerShell: `$env:GATE_MUTATION='changed'; docker compose ...`). Mutation is slow: warn the maintainer and do not launch more than 5 runs in parallel. If Docker is not available, **stop** and offer the maintainer: (a) installing it, (b) pushing and using CI as the gate, or (c) a partial run with `uv run pytest` explicitly accepted by them. In (b) and (c) the summary comment says "local gate not run".
3. Launch a re-reviewer subagent (medium Sonnet, read-only, different from those that wrote the code) with the list of original requests and the final diff. It returns a **verdict per comment**: resolved / not resolved / partially resolved, with `file:line` and evidence. It must not trust the fixers' summaries. Comments rejected with the maintainer are not scored: they are marked "rejected (maintainer)" and their threads are left open, quoted in the summary.
4. Check each review request yourself one by one against the verdict, and that no threshold was lowered and no `# pragma: no mutate` or `type: ignore` was added to dodge anything.
5. If something fails, go back to Phase 4 (it counts towards the 2-cycle cap). Report failures with the exact output. Do not declare success without evidence (`superpowers:verification-before-completion`).

## Phase 6 — Publish and close (each step with the maintainer's confirmation)

1. **Push**: normal `git push` if there was no rebase; if there was, `git push --force-with-lease=<branch>:<SHA>` with the SHA saved in Phase 3 and the maintainer's confirmation. If the remote is no longer at `<SHA>` or the push is rejected: stop, `git fetch`, show the maintainer the foreign commits (`git log <SHA>..origin/<branch>`) and go back to Phase 3. Never `--force`, and do not update the SHA without the maintainer seeing it.
2. **Wait for CI**: `gh pr checks <n> --watch --fail-fast` after the push. If it fails, go back to Phase 4. If CI does not start, tell the maintainer.
3. **Resolve applied threads** (only those the re-reviewer deemed resolved):
   ```bash
   gh api graphql -f query='mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{id isResolved}}}' -f id=<thread-id>
   ```
   Do not reply thread by thread.
4. **Re-request review** from the review's author: `gh pr edit <n> --add-reviewer <login>`. If the review's author is the PR's author (`gh pr view <n> --json author --jq .author.login`) or the command fails, do not retry: tell the maintainer and say so in the summary comment ("review pending re-review").
5. **Summary comment** in English: write it in the scratchpad and publish it with `gh pr comment <n> --body-file <path>` (avoids quoting and backtick problems). Content: what changed per comment, gate status, conflicts resolved, and what was rejected or left pending and why (decided with the maintainer). No attribution to Claude.
6. Verify the result with `gh pr view <n> --json reviews,reviewDecision,mergeStateStatus,statusCheckRollup`.

## Warning signs — STOP

| Thought | Reality |
|---|---|
| "The reviewer is right, I'll apply it" | Verify against the code; if doubtful, evidence and ask the maintainer. |
| "This comment is wrong, I'll ignore it" | Rejecting also requires asking, with evidence. |
| "I'll reply 'you're absolutely right'" | Performative answer. Technical action or evidence, nothing else. |
| "`--force` is faster" | Only `--force-with-lease`, with confirmation and checking that nobody else pushed. |
| "I'll resolve this conflict myself, it's obvious" | If it is semantic, stop and ask. Only trivial ones are resolved by the subagent. |
| "I'll fix first and rebase later" | Rebase before fixes, or you duplicate work. |
| "The subagent says it's fixed" | Demand red and green; without a visible red it is rejected. Read the diff. |
| "I'll rerun the job, it's surely flaky" | `gh run rerun --failed` only with demonstrated flakiness. Diagnose first. |
| "I'll lower the threshold / add `pragma: no mutate` to pass the gate" | Not without the maintainer. A survivor is a missing test. |
| "I'll commit and push, I'll tell them later" | Confirmation before each commit and each push. |
| "It's the third time I try, one more" | Cap of 2 failed cycles: consult the maintainer. |
| "I'll create a branch for this" / "I'm on `develop`, I'll continue" | Work on the PR's branch. If it is `main`/`develop`, stop. |
| "I'll add Co-Authored-By by default" | Forbidden in this repo, in commits and comments. |
| "Without Docker, I'll just run pytest" | Without Docker you stop and warn; there is no silent partial verification. |

## Common mistakes

- Context gathered in sequence instead of in parallel.
- Applying all comments without triage or grouping by files.
- Plan without model/effort per group.
- Launching more than 5 mutation runs in parallel.
- Resolving threads that the re-reviewer did not deem resolved.
- Forgetting to re-request review from the author or the summary comment.
- Addressing the maintainer by personal name in the comment instead of by role.
