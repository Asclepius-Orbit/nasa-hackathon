[English](README.md) · [Español](README.es.md) · **Català**

# spine-mcp-server

## Descripció

Servidor MCP (stdio) que exposa 7 eines de només lectura i simulació a l'agent LLM local. Mai no escriu dades, mai no toca la base de dades de l'app i mai no diagnostica. Avui funciona amb mocks construïts a partir dels exemples del contracte (`spine_monitor/contracts/examples/valid/`), darrere de tres Protocols (`AppReader`, `Simulator` i `AssessBaseSource` a `ports.py`) perquè els clients HTTP reals els puguin substituir més endavant.

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
- `simulate_scenario` és només hipotètic i mai no canvia dades reals.

## Errors

Una fallada de l'app o del motor (per exemple 404 no trobat o 422 rang no vàlid) es converteix en un error d'eina MCP (`isError: true`) amb el missatge `títol: detall`.

## Notes de compare_periods

- Les features es comparen per (`feature_id`, `protocol_id`, `gravity_environment`). Una clau amb més d'una unitat es deixa fora i es marca amb `unit_mismatch`.
- Els feature sets sense protocol o sense gravetat s'ignoren en la comparació, però es compten a `feature_sets_count`.
- Si dos assessments comparteixen `as_of`, `latest` és el nivell més greu.
- Les dades que falten són `null`, mai 0.
- `warnings` marca els períodes buits (`period_a_empty`, `period_b_empty`) i els truncats (`period_a_truncated`, `period_b_truncated`).

## Fora de l'abast per ara

- Clients HTTP reals, a l'espera de l'API de l'app (issue #17).
