[English](README.md) · **Español** · [Català](README.ca.md)

# nasa-hackathon

## Entorno de desarrollo (Python)

`spine_monitor/` es un workspace de uv: un solo `.venv` y un solo `uv.lock` para todos los paquetes. Trabaja siempre desde `spine_monitor/` (`uv sync --all-packages`, `uv run pytest`). En PyCharm, abre la carpeta `spine_monitor/` y elige como intérprete su `.venv`.

## Gateway del wearable

El gateway real del wearable (`spine_monitor/gateway`) lee el Arduino por USB serie y publica en el bus MQTT v1; el sketch está en `firmware/`. Una demo sin hardware ejecuta el gateway contra un emulador del Arduino:

```bash
docker compose -f app/docker-compose.yml --profile gateway-demo up -d arduino-emulator gateway-demo
```

El contexto de build es la raíz del repo: si Docker no puede leerlo por carpetas ilegibles (restos de directorios temporales en Windows), elimínalas o construye desde un clon limpio. Aún no se ha probado con una placa real. Ver [`spine_monitor/gateway/README.es.md`](spine_monitor/gateway/README.es.md) (ejecución, configuración, cableado y prueba con la placa) y [`firmware/README.es.md`](firmware/README.es.md) (sketch y protocolo serie).

## Skills de Claude Code para pull requests

Dos skills del proyecto en `.claude/skills/` llevan la revisión de PRs con subagentes:

- `review-pr`: revisa una PR. Primero lee la PR, la issue enlazada, el CI y los hilos; lanza revisores en paralelo (corrección, tests/TDD, seguridad), verifica cada bloqueante y publica una sola review con `gh`: request changes con sugerencias inline si hay bloqueantes, aprobación si solo hay fallos de estilo. En tu propia PR GitHub no permite un veredicto formal, así que publica una review `COMMENT` cuya primera línea es el veredicto.
- `receive-review`: aplica los cambios pedidos en una PR. Contrasta cada comentario con el código, hace rebase sobre `develop`, aplica los arreglos con TDD, arregla el CI roto, vuelve a pasar el gate de calidad, empuja con `--force-with-lease`, resuelve los hilos y re-solicita la review.

Ambas piden confirmación antes de publicar, commitear o empujar, y nunca añaden atribución a Claude.

## Skills y herramientas de terceros

Skills copiadas en `.claude/skills/` (procedencia y licencias en `.claude/skills/THIRD_PARTY.md`):

- `mcp-builder`: diseño de servidores MCP y sus evals (para `mcp_server`).
- `property-based-testing`: propiedades de Hypothesis para `physiology` y `engine`.
- `modern-python`: convenciones de uv y ruff. Su consejo de usar `ty` no aplica: este repo comprueba tipos con pyright.
- `async-python-patterns`, `python-testing-patterns`, `python-type-safety`: referencias de asyncio, pytest y tipado.

El `.mcp.json` del proyecto registra Context7 (versión fijada) para que Claude Code consulte documentación actual de FastAPI, aiomqtt, el SDK `mcp`, numpy y pydantic. Envía las consultas a Upstash.

Skills de proyecto para el dominio: `physiology-engine`, `contract-first`, `agent-evals`, `mqtt-dev` y `mcp-tool-change`.

`.claude/skills/ROUTING.md` asigna a cada tipo de tarea las skills que le aplican. `implement-feature`, `tdd-workflow`, `review-pr` y `receive-review` lo usan para nombrar esas skills en los prompts de los subagentes.

El tooling de Node usa solo pnpm (`pnpm install`, `pnpm run`, `pnpm dlx`); nunca npm ni npx. El lockfile es `app/ui/pnpm-lock.yaml`.

Dependencias nuevas: `pint` (unidades, en `physiology`), `pydantic-evals` (dev, para evals del agente), y `vitest` y `uplot` (dev, en `app/ui`).
