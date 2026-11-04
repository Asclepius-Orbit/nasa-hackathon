[English](README.md) · [Español](README.es.md) · **Català**

# spine_monitor

Workspace d'uv amb els paquets Python: `contracts`, `physiology`, `engine`, `mcp_server`, `agent` i `backend`.

## Qualitat i TDD

El codi de producció s'escriu test primer: un test en vermell visible, després verd, després refactor; un bugfix comença amb un test de regressió. El cicle està descrit a la skill del projecte `tdd-workflow` (`.claude/skills/tdd-workflow/`).

Bucle ràpid durant el desenvolupament:

```bash
cd spine_monitor && uv run pytest <ruta> -q -p no:randomly
```

Porta de qualitat (des de l'arrel del repo). Executa ruff, pyright i els tests amb cobertura (línies i branques >= 90 %, ordre aleatori, timeout, contra un mosquitto real):

```bash
docker compose -f app/docker-compose.yml run --rm quality
GATE_MUTATION=changed docker compose -f app/docker-compose.yml run --rm quality
GATE_MUTATION=full docker compose -f app/docker-compose.yml run --rm quality
```

Equivalent en PowerShell de la forma amb variable d'entorn:

```powershell
$env:GATE_MUTATION='changed'; docker compose -f app/docker-compose.yml run --rm quality
```

- La cobertura `fail_under` és una única xifra combinada de línies i branques (la cobertura de branques està activada): el llindar del 90 % s'aplica a aquest total combinat, no a cadascuna per separat.
- Després de canviar `pyproject.toml` o `uv.lock` cal reconstruir la imatge de qualitat: `docker compose -f app/docker-compose.yml build quality`, o afegir `--build` a `run`.
- El mutation testing no s'executa per defecte. Amb `GATE_MUTATION=changed` és incremental: només els fitxers canviats en el teu treball comitejat respecte a `origin/develop` (o respecte a `MUTATION_BASE` si està definida), i la seva puntuació de mutació agregada ha de ser >= 80 %.
- Amb `GATE_MUTATION=full` cobreix tots els paquets, només informa i mai falla; una execució completa de physiology triga unes 2 hores mesurades en una màquina de 22 CPU, i serà molt més lenta en un runner de CI de 2 vCPU.
- Marcadors: cada test porta exactament un de `unit`, `integration`, `contract`, `property` o `golden` (`mosquitto` és addicional). `conftest.py` assigna `unit` per defecte, `integration` als tests marcats amb `mosquitto` i `contract` als tests sota `contracts/tests`.
- Per regenerar a propòsit l'snapshot golden `physiology/tests/golden/tiny-mission.json`, executa el test golden amb `UPDATE_GOLDEN=1` i revisa el diff.
- CI: cada PR a `develop` executa la porta completa amb mutació incremental i exigeix cobertura del diff >= 90 % a les línies canviades; un workflow nocturn genera l'informe complet de mutació (informatiu, no bloquejant).
