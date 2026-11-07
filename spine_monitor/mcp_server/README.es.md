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

### Depurar con MCP Inspector

UI interactiva para llamar a las herramientas y leer sus esquemas. Se ejecuta bajo demanda con `pnpm dlx` y no es una dependencia del proyecto. Mantenla ligada a localhost.

```bash
pnpm dlx @modelcontextprotocol/inspector@2.14.0 uv run spine-mcp-server
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
- `overrides` es una lista opcional (vacía por defecto) de cambios con `feature_id`, `mode` (`set`, `add` o `scale`) y `value`; vacía u omitida significa sin cambios, así que se proyecta la trayectoria actual tal como está. `project_days` va de 1 a 365.
- `simulate_scenario` es solo hipotético y nunca cambia datos reales.

## Datasets

`python -m spine_mcp_server` sirve por defecto un **dataset de demo**: los últimos 14 días del astronauta `AST-01`, generados en relación con el reloj para que los datos terminen siempre «ahora». Está en verde los primeros 8 días y en ámbar los últimos 6, con un risk score creciente (de 0,18 a 0,62), una evaluación al día, una sesión de ejercicio cada 2 días más algunos chequeos de columna, un autoinforme diario (dolor NRS de 2 a 5) y un informe ODI semanal. `explain_assessment` funciona para todas las evaluaciones de ese histórico, `compare_periods` tiene datos en las dos semanas y `simulate_scenario` responde a los «¿y si…?» con un modelo lineal simple sobre la elongación lumbar (una aproximación, no el motor). `AST-01` es el único astronauta; cualquier otro id devuelve un error de herramienta «not found».

| Opción | Significado |
|---|---|
| `--dataset demo\|examples` | `demo` (por defecto), o `examples`: la foto estática única de `contracts/examples/valid/` que usan los tests de contratos |
| `--now 2026-11-13T12:00:00Z` | Fija el reloj del demo (ISO 8601 con zona horaria, redondeado a la hora) para demos y tests reproducibles; sin él se usa el reloj de pared |

Los datos del demo se generan de forma determinista: el mismo `--now` da siempre los mismos payloads e ids.

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
