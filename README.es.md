[English](README.md) · **Español** · [Català](README.ca.md)

# nasa-hackathon

## Entorno de desarrollo (Python)

`spine_monitor/` es un workspace de uv: un solo `.venv` y un solo `uv.lock` para todos los paquetes. Trabaja siempre desde `spine_monitor/` (`uv sync --all-packages`, `uv run pytest`). En PyCharm, abre la carpeta `spine_monitor/` y elige como intérprete su `.venv`.

## Skills de Claude Code para pull requests

Dos skills del proyecto en `.claude/skills/` llevan la revisión de PRs con subagentes:

- `review-pr`: revisa una PR. Primero lee la PR, la issue enlazada, el CI y los hilos; lanza revisores en paralelo (corrección, tests/TDD, seguridad), verifica cada bloqueante y publica una sola review con `gh`: request changes con sugerencias inline si hay bloqueantes, aprobación si solo hay fallos de estilo. En tu propia PR GitHub no permite un veredicto formal, así que publica una review `COMMENT` cuya primera línea es el veredicto.
- `receive-review`: aplica los cambios pedidos en una PR. Contrasta cada comentario con el código, hace rebase sobre `develop`, aplica los arreglos con TDD, arregla el CI roto, vuelve a pasar el gate de calidad, empuja con `--force-with-lease`, resuelve los hilos y re-solicita la review.

Ambas piden confirmación antes de publicar, commitear o empujar, y nunca añaden atribución a Claude.
