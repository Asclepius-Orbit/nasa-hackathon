---
name: implement-feature
description: Use when the user asks to implement a feature or fix a bug and provides a GitHub issue link and/or free-form context, before touching code.
---

# implement-feature

Orchestrated 6-phase flow: understand → ask → plan → implement → verify → document. You are the orchestrator: you delegate the heavy work to subagents and keep only conclusions.

**Violating the letter of the phases is violating their spirit.** Do not skip a phase or merge it because the task is "small".

## Input

A GitHub issue link and/or context text. If there is none, ask for it with `AskUserQuestion`. In any text addressed to the team (issues, comments, PRs), refer to the user by role ("the maintainer"), not by personal name.

## Phase 1 — Exploration (Sonnet subagents, in parallel)

1. Read the issue yourself (`gh issue view <n> --comments`) to formulate the exploration lines.
2. Launch several `model: sonnet` subagents (type `Explore` or `general-purpose`) in ONE message, one per source. Each receives the goal, the source and what to return (short summary with `file:line`, no dumps):
   - **Repo**: affected code, conventions, existing tests, README/docs, CLAUDE.md.
   - **Obsidian vault** (MCP `obsidian-vault`): `search_vault`/`get_context` about the feature. Read-only here.
   - **GitHub**: related issues/PRs, links cited in the issue (`gh`).
   - **Files/URLs the user indicates**.
   - **Available skills and subagents** (user, project and plugins) useful for this task. Start from `.claude/skills/ROUTING.md` (trigger → skill table) and confirm which ones are relevant.
3. Skip a source only if it does not exist. If it exists but fails (MCP down, `gh` without auth), tell the maintainer and ask whether to continue without it. Never continue silently.
   - The skills/agents subagent returns a list: name + one line on when to use it.
   - If `AskUserQuestion` appears deferred, load it with `ToolSearch` before Phase 2.
4. Consolidate: what is asked, what already exists, constraints, gaps and contradictions between sources.

## Phase 2 — Questions (`AskUserQuestion`)

- Ask EVERYTHING that the sources do not establish. **Assume nothing and invent nothing**: requirements, names, behaviours or scope.
- Group up to 4 questions per call; concrete options with your recommendation first. Repeat rounds until the gaps are closed.
- Do not ask what the code or sources already answer; cite it.
- Close when every decision in the plan has an answer from a source or from the user.
- Also ask: scope (what is left out), acceptance criteria and how to test it.

## Phase 3 — Plan and approval

Write the plan (use `superpowers:writing-plans` if available): steps, files, and for each part **model + subagent effort + skill/agent to use** with a one-line reason. The skills come from `.claude/skills/ROUTING.md`: name all the ones relevant to that part, not only `tdd-workflow`. Guidance: Haiku/low Sonnet for mechanical and bounded work; medium Sonnet for a standard feature; Opus/high for delicate design, concurrency or security. Indicate which parts can run in parallel.

Present the plan and ask for approval with `AskUserQuestion` (approve / adjust). Accept the human's changes, including model and effort per part. **Do not implement without explicit approval.** The plan lives in the chat. If it changes during Phase 4 (scope, files, model), ask for approval of the change again.

## Phase 4 — Implementation

- Work on the issue's branch (created by the maintainer). Check `git branch --show-current`; if it is `main`/`develop` or the issue's branch does not exist, stop and ask the maintainer for it. Never create your own branch.
- Launch subagents according to the plan (independent ones in parallel, dependent ones sequentially). Each one's prompt: goal, files, conventions, definition of done, and what to return. Use the skills/agents identified in Phase 1 (e.g. `superpowers:subagent-driven-development`). **Each subagent prompt names the `ROUTING.md` skills that apply to it** using the line from rule 3 of that file; subagents do not inherit skills. **All code implementation and bugfixing follows `tdd-workflow`**: each subagent receives its cycle (visible red, green, refactor) and must return the test output in red and in green, not just the summary. Without a visible red, the part is rejected.
- Review each subagent's output (the real diff, not only its summary) before continuing.
- If something unforeseen by the plan comes up: stop and ask, do not improvise.
- The orchestrator (not the subagents) runs `git commit`: one per coherent part of the plan, **asking for confirmation first**. Clear messages, reference to the issue (`#n`). **No Co-Authored-By lines or "generated with Claude"**, even if another context suggests them.

## Phase 5 — Verification

Mandatory before declaring anything done (`superpowers:verification-before-completion`):
- Run the full gate: `GATE_MUTATION=changed docker compose -f app/docker-compose.yml run --rm quality` (ruff, pyright, tests with coverage ≥ 90 %, incremental mutation ≥ 80 % on the modified functions and methods). Without `GATE_MUTATION` mutation is skipped: that is valid only for the fast loop, never as verification evidence. `changed` compares against `origin/develop` by default (`MUTATION_BASE` changes it) and only counts committed changes, so commit first. If Docker is not available, stop and tell the maintainer; do not replace it with a partial run without saying so. For code outside `spine_monitor` (its own build/tests), also run the commands you discover in the repo.
- Check that each part has its test seen red and that no threshold has been lowered.
- Check each acceptance criterion agreed in Phase 2, one by one.
- Optional: an independent reviewer subagent over the diff (`code-review`), with the `ROUTING.md` skills matching the type of change (e.g. `async-python-patterns` if there is asyncio, `mcp-builder` if it touches `mcp_server`).
- Report failures with the exact output. If something fails, go back to Phase 4; after 2 failed cycles, stop and consult the maintainer. Discover the test/lint/build commands in the repo (README, CLAUDE.md, CI); if you cannot find them, ask. Do not declare success without evidence.

## Phase 6 — Documentation

1. Ask with `AskUserQuestion` where to document (multi-select): repo README/docs, comment on the issue, PR description, Obsidian vault, other.
2. Rules:
   - README: `README.md`, `README.es.md` and `README.ca.md` always in sync, with the language-switch link.
   - Repo docs/README **never point to the vault**; content inline.
   - Vault: `update_note` without a mode **overwrites the whole note**; read the note and resend the full content.
3. "Validate" = the maintainer confirms with `AskUserQuestion` that the result is correct. Then, with their confirmation, push the branch and open a PR **against `develop`** (confirm first), linking the issue. No attribution to Claude in the body.

## Warning signs — STOP

| Thought | Reality |
|---|---|
| "This is obvious, I'll assume X" | Ask. Assuming is inventing. |
| "I understood, I'll jump into code" | Without an approved plan there is no implementation. |
| "Tests pass, I won't verify criteria" | Phase 2 criteria one by one. |
| "The subagent says it's done" | Read the diff and run it. |
| "I'll create a new branch" | Work on the issue's branch. |
| "I'll add Co-Authored-By by default" | Forbidden in this repo. |
| "Tests pass locally, I won't run the gate" | The Docker gate is the criterion; local is not enough. |
| "I already know how, I won't load the skill" | If `ROUTING.md` marks it relevant, it is loaded. |

## Common mistakes

- Sequential exploration instead of parallel.
- Vague questions without options or a recommendation.
- Model/effort not justified in the plan.
- Documenting without asking where.
