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
- El mutation testing no s'executa per defecte. Amb `GATE_MUTATION=changed` és incremental: només les funcions i els mètodes amb línies canviades en el teu treball comitejat respecte a `origin/develop` (o respecte a `MUTATION_BASE` si està definida), i la seva puntuació de mutació agregada ha de ser >= 80 %. Les línies canviades fora de qualsevol funció (imports, constants, atributs de classe) no afegeixen mutants, perquè mutmut no les muta mai. Mostra mínima (decisió conscient d'Uri): quan `changed` puntua menys de 10 mutants (`MUTATION_MIN_SAMPLE`, 0 desactiva la regla), una puntuació per sota del 80 % s'imprimeix amb l'avís `score shown, not enforced` i el codi de sortida és 0, perquè un mutant que canvia mou la puntuació 100/N punts i el soroll mesurat és d'uns 3 mutants per execució; per tant, un canvi que selecciona menys de 10 mutants pot passar sense que els seus tests els matin. Els errors d'infraestructura continuen fent fallar la porta.
- Amb `GATE_MUTATION=full` cobreix tots els paquets, només informa i mai falla; una execució completa de physiology triga unes 2 hores mesurades en una màquina de 22 CPU, i s'estima un ordre de magnitud més lenta en un runner allotjat de 2 vCPU, per sobre del màxim de 360 minuts del job, així que s'executa en local amb `GATE_MUTATION=full docker compose -f app/docker-compose.yml run --rm quality`. El workflow `full-mutation` és només manual (`workflow_dispatch`), per a un runner propi o més gran o per a un sol paquet (entrada `packages`, reenviada com a `MUTATION_PACKAGES`).
- Les execucions de mutació (`changed` i `full`) no muten les crides de logging: els arguments de sentències com `_log.warning("...", x)` (text del missatge, arguments de format, `exc_info`) no canvien cap comportament que els tests observin, així que `quality/mutation.sh` genera el `do_not_mutate_patterns` de mutmut per saltar exactament les sentències que comencen una crida `debug`/`info`/`warning`/`warn`/`error`/`exception`/`critical`/`log` sobre un objecte `log`, `_log`, `logger` o `_logger`. No se salta res més i el codi de producció no du `# pragma: no mutate`. Avui això treu 125 dels 8.993 mutants de physiology, tots a `gateway/app.py`.
- Marcadors: cada test porta exactament un de `unit`, `integration`, `contract`, `property` o `golden` (`mosquitto` és addicional). `conftest.py` assigna `unit` per defecte, `integration` als tests marcats amb `mosquitto` i `contract` als tests sota `contracts/tests`.
- Per regenerar a propòsit l'snapshot golden `physiology/tests/golden/tiny-mission.json`, executa el test golden amb `UPDATE_GOLDEN=1` i revisa el diff.
- CI: cada PR a `develop` executa la porta completa amb mutació incremental i exigeix cobertura del diff >= 90 % a les línies canviades; l'informe complet de mutació s'executa a mà (vegeu més amunt), no de manera programada. El job de PR té un timeout de 240 minuts.
