[English](README.md) · **Español** · [Català](README.ca.md)

# spine-mcp-server

## Descripción

Servidor MCP (stdio) que expone 7 herramientas de solo lectura y simulación al agente LLM local. Nunca escribe datos, nunca toca la base de datos de la app y nunca diagnostica. Hoy funciona con mocks construidos a partir de los ejemplos del contrato (`spine_monitor/contracts/examples/valid/`), detrás de tres Protocols (`AppReader`, `Simulator` y `AssessBaseSource` en `ports.py`) para que los clientes HTTP reales puedan sustituirlos más adelante.

## Ejecución

Desde `spine_monitor/`:

```bash
uv run spine-mcp-server          # o: uv run python -m spine_mcp_server
uv run pytest mcp_server         # tests
```

## Herramientas

| Herramienta | Parámetros | Devuelve (esquema del contrato) |
|---|---|---|
| `get_current_status` | `astronaut_id` | `app/status` |
| `get_history` | `astronaut_id`, `start`, `end`, `limit=100` | `app/history` |
| `get_sessions` | `astronaut_id`, `start`, `end`, `limit=100` | `app/session-list` |
| `get_self_reports` | `astronaut_id`, `start`, `end`, `limit=100` | `app/self-report-list` |
| `compare_periods` | `astronaut_id`, `a_start`, `a_end`, `b_start`, `b_end` | `mcp/compare-periods` |
| `explain_assessment` | `assessment_id` | `app/explanation` |
| `simulate_scenario` | `astronaut_id`, `overrides`, `project_days=None` | `engine/simulate-response` |

- `start` y `end` son timestamps RFC 3339 con desfase UTC. `limit` va de 1 a 500.
- `compare_periods` lee ambos periodos con `limit` 500 y calcula la comparación en el servidor. Los deltas son B menos A.
- `overrides` es una lista de cambios con `feature_id`, `mode` (`set`, `add` o `scale`) y `value`. `project_days` va de 1 a 365.
- `simulate_scenario` es solo hipotético y nunca cambia datos reales.

## Errores

Un fallo de la app o del motor (por ejemplo 404 no encontrado o 422 rango no válido) se convierte en un error de herramienta MCP (`isError: true`) con el mensaje `título: detalle`.

## Notas de compare_periods

- Las features se comparan por (`feature_id`, `protocol_id`, `gravity_environment`). Una clave con más de una unidad se deja fuera y se marca con `unit_mismatch`.
- Los feature sets sin protocolo o sin gravedad se ignoran en la comparación, pero se cuentan en `feature_sets_count`.
- Si dos assessments comparten `as_of`, `latest` es el nivel más grave.
- Los datos que faltan son `null`, nunca 0.
- `warnings` marca los periodos vacíos (`period_a_empty`, `period_b_empty`) y los truncados (`period_a_truncated`, `period_b_truncated`).

## Fuera de alcance por ahora

- Clientes HTTP reales, a la espera de la API de la app (issue #17).
