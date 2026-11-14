[English](README.md) · [Español](README.es.md) · **Català**

# nasa-hackathon

## Antropometria i evidència fora de línia

L'[inventari de fonts](docs/anthropometry-sources.md) recull guies de disseny de la NASA i un estudi relacionat, amb població, mètode, pàgines, limitacions i condicions de reutilització. Els valors de la NASA d'aproximadament el 3 % de l'estatura dreta i el 6 % de l'altura asseguda són marges de disseny de naus, no llindars clínics. Una predicció del motor és experimental: depèn de la referència terrestre individual en la mateixa postura, de la incertesa del sensor i de les hipòtesis indicades; estatura, altura asseguda i longitud estimada del cordó són magnituds diferents.

Prepara l'equip amb connexió: instal·la les dependències fixades i les imatges Docker, configura el broker com s'indica a continuació i desa el model Ollama necessari. La [guia de l'agent](spine_monitor/agent/README.ca.md) descriu el volum de models i la configuració. Conserva aquest volum i els recursos d'evidència locals quan traslladis o actualitzis una instal·lació fora de línia; `docker compose down -v` esborra el volum de models. Comprova que el model exacte i els recursos hi siguin abans de desconnectar. El servei actual `ollama-pull` intenta descarregar els models absents, per tant no s'ha d'invocar sense connexió; sense model local no es pot generar amb aquest. Un informe generat ha de citar ubicacions comprovables de les fonts o indicar que l'evidència és insuficient.

El corpus inclòs i el seu índex vectorial lèxic dispers són fitxers locals. Després d'actualitzar el corpus, reconstrueix l'índex corresponent des de `spine_monitor/` (no cal Internet):

```bash
uv run python -c "from pathlib import Path; from spine_mcp_server.evidence import build_index; p=Path('mcp_server/src/spine_mcp_server/evidence_data'); build_index(p/'corpus.json', p/'index.json')"
```

Copia tots dos fitxers junts en instal·lar o actualitzar fora de línia. `SPINE_EVIDENCE_DIR` pot apuntar al seu directori. Si falten, són invàlids o no coincideixen, la recuperació falla de manera explícita; l'índex no descarrega evidència ni proporciona embeddings semàntics.

## Entorn de desenvolupament (Python)

`spine_monitor/` és un workspace d'uv: un sol `.venv` i un sol `uv.lock` per a tots els paquets. Treballa sempre des de `spine_monitor/` (`uv sync --all-packages`, `uv run pytest`). A PyCharm, obre la carpeta `spine_monitor/` i tria com a intèrpret el seu `.venv`.

Docker Compose (`app/docker-compose.yml`): el broker MQTT exigeix usuaris i ACL, i les seves credencials mai no són al repo. Executa `sh app/mosquitto/setup.sh` un cop abans de qualsevol `docker compose` (Git Bash a Windows); les escriu a `app/.env` (al `.gitignore`). Els usuaris, l'alta de dispositius i l'accés per la LAN amb TLS són a [spine_monitor/backend/README.ca.md](spine_monitor/backend/README.ca.md).
## Gateway del wearable

El gateway real del wearable (`spine_monitor/gateway`) llegeix l'Arduino per USB sèrie i publica al bus MQTT v1; el sketch és a `firmware/`. Una demo sense hardware executa el gateway contra un emulador de l'Arduino:

```bash
docker compose -f app/docker-compose.yml --profile gateway-demo up -d arduino-emulator gateway-demo
```

El context de build és l'arrel del repo: si Docker no pot llegir-lo per carpetes il·legibles (restes de directoris temporals a Windows), elimina-les o construeix des d'un clon net. Encara no s'ha provat amb una placa real. Vegeu [`spine_monitor/gateway/README.ca.md`](spine_monitor/gateway/README.ca.md) (execució, configuració, cablejat i prova amb la placa) i [`firmware/README.ca.md`](firmware/README.ca.md) (sketch i protocol sèrie).

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

## Llicència

Tots els drets reservats fins al **15-11-2026 a les 16:00 (Europe/Madrid)**; a partir d'aquell moment el repositori es llicencia sota la GNU General Public License v3. Els termes són a [`LICENSE`](LICENSE). El material de tercers, com tot el que hi ha a `.claude/skills/`, manté la seva pròpia llicència i no està cobert (vegeu [`.claude/skills/THIRD_PARTY.md`](.claude/skills/THIRD_PARTY.md)).
