[English](README.md) · [Español](README.es.md) · **Català**

# nasa-hackathon

## Entorn de desenvolupament (Python)

`spine_monitor/` és un workspace d'uv: un sol `.venv` i un sol `uv.lock` per a tots els paquets. Treballa sempre des de `spine_monitor/` (`uv sync --all-packages`, `uv run pytest`). A PyCharm, obre la carpeta `spine_monitor/` i tria com a intèrpret el seu `.venv`.

## Skills de Claude Code per a pull requests

Dues skills del projecte a `.claude/skills/` porten la revisió de PRs amb subagents:

- `review-pr`: revisa una PR. Primer llegeix la PR, la issue enllaçada, el CI i els fils; llança revisors en paral·lel (correcció, tests/TDD, seguretat), verifica cada bloquejant i publica una sola review amb `gh`: request changes amb suggeriments inline si hi ha bloquejants, aprovació si només hi ha errors d'estil. A la teva pròpia PR GitHub no permet un veredicte formal, així que publica una review `COMMENT` la primera línia de la qual és el veredicte.
- `receive-review`: aplica els canvis demanats en una PR. Contrasta cada comentari amb el codi, fa rebase sobre `develop`, aplica les correccions amb TDD, arregla el CI trencat, torna a passar el gate de qualitat, empeny amb `--force-with-lease`, resol els fils i re-sol·licita la review.

Totes dues demanen confirmació abans de publicar, fer commit o empènyer, i mai afegeixen atribució a Claude.

## Skills i eines de tercers

Skills copiades a `.claude/skills/` (procedència i llicències a `.claude/skills/THIRD_PARTY.md`):

- `mcp-builder`: disseny de servidors MCP i els seus evals (per a `mcp_server`).
- `property-based-testing`: propietats de Hypothesis per a `physiology` i `engine`.
- `modern-python`: convencions d'uv i ruff. El seu consell d'usar `ty` no aplica: aquest repo comprova tipus amb pyright.
- `async-python-patterns`, `python-testing-patterns`, `python-type-safety`: referències d'asyncio, pytest i tipat.

El `.mcp.json` del projecte registra Context7 (versió fixada) perquè Claude Code consulti documentació actual de FastAPI, aiomqtt, l'SDK `mcp`, numpy i pydantic. Envia les consultes a Upstash.

Skills de projecte per al domini: `physiology-engine`, `contract-first`, `agent-evals`, `mqtt-dev` i `mcp-tool-change`.

`.claude/skills/ROUTING.md` assigna a cada tipus de tasca les skills que li apliquen. `implement-feature`, `tdd-workflow`, `review-pr` i `receive-review` l'usen per anomenar aquestes skills als prompts dels subagents.

El tooling de Node usa només pnpm (`pnpm install`, `pnpm run`, `pnpm dlx`); mai npm ni npx. El lockfile és `app/ui/pnpm-lock.yaml`.

Dependències noves: `pint` (unitats, a `physiology`), `pydantic-evals` (dev, per als evals de l'agent), i `vitest` i `uplot` (dev, a `app/ui`).
