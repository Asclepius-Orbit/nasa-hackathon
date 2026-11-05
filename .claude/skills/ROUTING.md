# Enrutado de skills del repo

Tabla única que usan `implement-feature`, `tdd-workflow`, `review-pr` y `receive-review`. Cuando una skill del repo encaje con la tarea, el orquestador la nombra en el plan o en el prompt del subagente, y el subagente la carga con la herramienta `Skill` **antes de empezar**. Los subagentes no heredan las skills: si el prompt no las nombra, no las usan.

"Pertinente" = coincide el disparador de la fila. No cargues una skill por si acaso, ni las cargues todas.

## Tabla

| Disparador (qué toca la tarea) | Skill(s) | Para qué |
|---|---|---|
| Cualquier código de producción Python (feature, bugfix, refactor) | `tdd-workflow` | Ciclo rojo/verde/refactor y gate. Siempre. |
| Tests nuevos o cambiados con pytest: fixtures, parametrize, mocks | `python-testing-patterns` | Patrones de fixtures y dobles. Manda `tdd-workflow` (markers, dobles solo en fronteras). |
| Invariantes, ida y vuelta (serializar/parsear), normalizadores, validadores, numéricos, scoring, curvas, rng en `physiology`, `engine`, `contracts` | `property-based-testing` | Propiedades Hypothesis (marker `property`). |
| `async def`, `asyncio`, aiomqtt, httpx async, FastAPI async, tareas y cancelación | `async-python-patterns` | Concurrencia correcta, sin bloquear el loop. |
| Anotaciones, genéricos, Protocols (`ports.py`), errores de pyright | `python-type-safety` | Tipado estricto. Solo `# pyright: ignore[regla]` con motivo. |
| `pyproject.toml`, dependencias, uv, ruff, scripts, workspace | `modern-python` | Convenciones de uv y ruff. **Ignora** su consejo de `ty`, prek y los shims: aquí se usa pyright. |
| `mcp_server`: añadir o cambiar una tool MCP (parámetros, salida, errores) | `mcp-tool-change` + `mcp-builder` | Pasos del repo (puerto, mock, tests, READMEs, recuento) y criterio de diseño de tools. |
| Agente LLM (`agent`): prompt, modelo, tools que usa o sus evals | `agent-evals` | Evals de regresión con pydantic-evals, offline por defecto. |
| Llamadas a la API de Claude | `claude-api` (de usuario) | Modelos, parámetros, caché. |
| UI (`app/ui`): pantallas, componentes, estilos | `frontend-design`, `impeccable`, `ui-ux-pro-max` | Dirección visual, UX, accesibilidad. |
| UI: tests de componentes o gráficas de telemetría | `vitest`, `uplot` (dependencias, no skills) | Tests TS y streaming de series. |
| Consultar documentación de una librería (FastAPI, aiomqtt, `mcp`, numpy, pydantic) | MCP `context7` | Docs actuales; el modelo puede ir desfasado con Python 3.14. |
| Bug o test que falla sin causa clara | `superpowers:systematic-debugging` | Causa raíz antes de arreglar. |
| Diseño de una feature nueva o comportamiento por definir | `superpowers:brainstorming` | Antes de planificar. |
| Contratos JSON Schema / OpenAPI / AsyncAPI, ejemplos, modelos o tipos TS generados | `contract-first` | Orden: contrato, ejemplos, regenerar, tests de contrato, drift. |
| Modelo, señal, feature, curva, fallo, escenario o scoring en `physiology` / `engine`; cambia el golden | `physiology-engine` | Determinismo con semilla, unidades, invariantes, golden. |
| MQTT: broker Mosquitto, topics, gateway, ingest del backend, tests con broker | `mqtt-dev` | Broker local, inspección, convención de topics y tests. |

## Reglas

1. **Mandan las convenciones del repo** (`tdd-workflow`, README, `pyproject`, pyright, umbrales) sobre cualquier consejo de una skill de terceros. Si chocan, gana el repo y se anota.
2. **Skills de terceros** (`mcp-builder`, `property-based-testing`, `modern-python`, `async-python-patterns`, `python-testing-patterns`, `python-type-safety`): léelas como referencia, no ejecutes scripts suyos sin revisarlos (ver `THIRD_PARTY.md`).
3. **Un prompt de subagente** que use skills incluye la línea: «Antes de empezar, invoca con la herramienta Skill: `<skill>`, `<skill>`. Si chocan con `tdd-workflow` o el README, manda el repo.»
4. **Node: solo pnpm.** Nunca `npm` ni `npx`: `pnpm install`, `pnpm run <script>`, `pnpm dlx <paquete>` (y `pnpm add -D --save-exact`). Lockfile: `pnpm-lock.yaml`. Si una skill de terceros dice `npm`/`npx`, tradúcelo.
5. **Skill nueva en el repo**: añade su fila aquí, en el mismo commit.
