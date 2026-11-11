[English](README.md) · [Español](README.es.md) · **Català**

# spine-mcp-server

## Descripció

Servidor MCP (stdio) que exposa 7 eines de només lectura i simulació a l'agent LLM local. Mai no escriu dades, mai no toca la base de dades de l'app i mai no diagnostica. Per defecte funciona amb mocks construïts a partir dels exemples del contracte (`spine_monitor/contracts/examples/valid/`); amb `--dataset http` llegeix de l'API de lectura real de l'app. En tots dos casos les dades entren darrere de tres Protocols (`AppReader`, `Simulator` i `AssessBaseSource` a `ports.py`), així que la font es pot substituir.

## Execució

Des de `spine_monitor/`:

```bash
uv run spine-mcp-server          # o: uv run python -m spine_mcp_server
uv run pytest mcp_server         # tests
```

### Depurar amb MCP Inspector

UI interactiva per cridar les eines i llegir-ne els esquemes. S'executa sota demanda amb `pnpm dlx` i no és una dependència del projecte. Mantén-la lligada a localhost.

```bash
pnpm dlx @modelcontextprotocol/inspector@2.14.0 uv run spine-mcp-server
```

## Eines

| Eina | Paràmetres | Retorna (esquema del contracte) |
|---|---|---|
| `get_current_status` | `astronaut_id` | `app/status` |
| `get_history` | `astronaut_id`, `start`, `end`, `limit=100` | `app/history` |
| `get_sessions` | `astronaut_id`, `start`, `end`, `limit=100` | `app/session-list` |
| `get_self_reports` | `astronaut_id`, `start`, `end`, `limit=100` | `app/self-report-list` |
| `compare_periods` | `astronaut_id`, `a_start`, `a_end`, `b_start`, `b_end` | `mcp/compare-periods` |
| `explain_assessment` | `assessment_id` | `app/explanation` |
| `simulate_scenario` | `astronaut_id`, `overrides`, `project_days=None` | `engine/simulate-response` |

- `start` i `end` són timestamps RFC 3339 amb desfasament UTC. `limit` va de 1 a 500.
- `compare_periods` llegeix els dos períodes amb `limit` 500 i calcula la comparació al servidor. Els deltes són B menys A.
- `overrides` és una llista opcional (buida per defecte) de canvis amb `feature_id`, `mode` (`set`, `add` o `scale`) i `value`; buida o omesa vol dir sense canvis, així que es projecta la trajectòria actual tal com és. `project_days` va de 1 a 365.
- `simulate_scenario` és només hipotètic i mai no canvia dades reals. Amb `--dataset http` la seva base és la petició de valoració que l'app enviaria ara al motor (`GET /crew/{astronaut_id}/assess-base`, `as_of` = el rellotge de l'app, darrers 14 dies de dades); si l'app no la pot construir (sense perfil, sense baseline o sense feature sets a la finestra) l'eina retorna un error 404 que anomena el motiu.

## Datasets

`python -m spine_mcp_server` serveix per defecte un **dataset de demo**: els últims 14 dies de l'astronauta `AST-01`, generats en relació amb el rellotge perquè les dades acabin sempre «ara». Està en verd els primers 8 dies i en ambre els últims 6, amb un risk score creixent (de 0,18 a 0,62), una avaluació al dia, una sessió d'exercici cada 2 dies més alguns xequejos de columna, un autoinforme diari (dolor NRS de 2 a 5) i un informe ODI setmanal. `explain_assessment` funciona per a totes les avaluacions d'aquest històric, `compare_periods` té dades a les dues setmanes i `simulate_scenario` respon als «i si…?» amb un model lineal simple sobre l'elongació lumbar (una aproximació, no el motor). `AST-01` és l'únic astronauta; qualsevol altre id retorna un error d'eina «not found».

Per llegir l'app real, fes servir `--dataset http`. Crida l'API de lectura de l'app a `SPINE_APP_URL` (per defecte `http://127.0.0.1:8002`) i configura el simulador per cridar directament el motor a `SPINE_ENGINE_URL` (per defecte `http://127.0.0.1:8001`). La base de valoració ve de l'API de lectura de l'app (`/crew/{astronaut_id}/assess-base`).

```bash
SPINE_APP_URL=http://127.0.0.1:8002 uv run python -m spine_mcp_server --dataset http
```

| Opció | Significat |
|---|---|
| `--dataset demo\|examples\|http` | `demo` (per defecte); `examples`: la foto estàtica única de `contracts/examples/valid/` que fan servir els tests de contractes; `http`: l'API de lectura real de l'app a `SPINE_APP_URL` |
| `SPINE_APP_URL` | URL base de l'API de lectura de l'app que usa `http` (per defecte `http://127.0.0.1:8002`) |
| `SPINE_ENGINE_URL` | URL base del motor que usa el simulador HTTP (per defecte `http://127.0.0.1:8001`) |
| `--now 2026-11-13T12:00:00Z` | Fixa el rellotge del demo (ISO 8601 amb zona horària, arrodonit a l'hora) per a demos i tests reproduïbles; sense ell s'usa el rellotge de paret |

Les dades del demo es generen de forma determinista: el mateix `--now` dona sempre els mateixos payloads i ids.

## Errors

Una fallada de l'app o del motor (per exemple 404 no trobat o 422 rang no vàlid) es converteix en un error d'eina MCP (`isError: true`) amb el missatge `títol: detall`.

Amb `--dataset http`, les fallades de l'API de l'app es tradueixen així:

| Fallada upstream | Error d'eina MCP |
|---|---|
| 4xx amb problem details (per exemple 404 o 422) | `títol: detall`, es conserva l'estat upstream; sense `detail`, el substitueixen els errors de camp (`pointer: message`); retallat a 500 caràcters |
| Altres no èxits per sota de 500 (4xx, 3xx), sense cos problem | `App API request rejected`, es conserva l'estat upstream |
| 5xx | `App API error: HTTP <estat>`, es conserva l'estat upstream; no es reenvia el detall upstream |
| Cos de resposta que no compleix el contracte | 502, `App API returned an invalid response` |
| Sense resposta en 10 segons | 504, `App API timeout` |
| Fallada de connexió | 503, `App API unavailable` |

El simulador aplica les mateixes traduccions als errors del motor, identificant-lo com a servei upstream.

## Notes de compare_periods

- Les features es comparen per (`feature_id`, `protocol_id`, `gravity_environment`). Una clau amb més d'una unitat es deixa fora i es marca amb `unit_mismatch`.
- Els feature sets sense protocol o sense gravetat s'ignoren en la comparació, però es compten a `feature_sets_count`.
- Si dos assessments comparteixen `as_of`, `latest` és el nivell més greu.
- Les dades que falten són `null`, mai 0.
- `warnings` marca els períodes buits (`period_a_empty`, `period_b_empty`) i els truncats (`period_a_truncated`, `period_b_truncated`).

## Fora de l'abast per ara

- El demo continua sent el dataset per defecte fins que el motor sigui real.
