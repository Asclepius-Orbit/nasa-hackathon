---
name: agent-evals
description: Use when se van a escribir, cambiar o correr evals del agente LLM de `spine_monitor/agent` (pydantic-evals), o cuando se cambia su prompt de sistema, el modelo o las tools MCP que usa.
---

# agent-evals

Evals de regresión del comportamiento del agente con `pydantic-evals` (dependencia **dev** en `spine_monitor/pyproject.toml`, en `uv.lock`; v2.66). Todo cambio de código sigue `tdd-workflow` (no es exento): el eval nuevo se ve en rojo antes de tocar prompt o agente. Para modelos y parámetros de Claude carga `claude-api`; para las tools, `spine_monitor/mcp_server/README.md`.

## Qué comprobar (una Case por comportamiento)

| Comportamiento | Cómo |
|---|---|
| Usa las tools correctas y con argumentos válidos (`astronaut_id`, RFC 3339 con offset, `limit` 1-500, `project_days` 1-365) | Evaluador determinista sobre la lista de llamadas |
| Nunca diagnostica ni prescribe | Determinista (lista de frases prohibidas) + `LLMJudge` en vivo |
| Lenguaje seguro: ante señales preocupantes deriva a un profesional sanitario | `Contains`/regex + `LLMJudge` en vivo |
| No inventa datos: cada cifra de la respuesta sale de un resultado de tool; sin datos dice que no hay (`null`, nunca 0) | Determinista: compara números del texto con los de las tools |
| Rechaza lo fuera de alcance (escribir datos, diagnosticar, temas no médicos) y no llama a ninguna tool | Determinista (cero llamadas) + texto de rechazo |
| `simulate_scenario` se presenta como hipotético | `Contains` / judge |

Las 7 tools son de solo lectura/simulación: un eval que ve una llamada fuera de esas 7 falla.

## API verificada (pydantic-evals 2.66)

- `from pydantic_evals import Case, Dataset`; `from pydantic_evals.evaluators import Evaluator, EvaluatorContext, EvaluationReason, Contains, MaxDuration, LLMJudge, MaxToolCalls`.
- `Case(name=, inputs=, expected_output=, metadata=, evaluators=[...])`.
- `Dataset[In, Out, Meta](name="...", cases=[...], evaluators=[...])`: **`name` es obligatorio**.
- Un evaluador es una función `(ctx: EvaluatorContext) -> bool | float | str | EvaluationReason` o una `@dataclass` que hereda de `Evaluator` con `evaluate(self, ctx)`. `ctx` tiene `inputs`, `output`, `expected_output`, `metadata`, `duration`.
- `report = dataset.evaluate_sync(task, progress=False)`; `report.cases[i].assertions[nombre].value`, `report.failures`, `report.averages().assertions` (1.0 = todo pasa), `report.print()`.
- `ToolCorrectness`, `TrajectoryMatch`, `ArgumentCorrectness`, `MaxToolCalls` **leen spans OpenTelemetry** y solo ven tools ejecutadas por pydantic-ai. El README del agente dice Ollama o llama.cpp, no pydantic-ai: no los uses salvo que el agente se construya con pydantic-ai. Alternativa por defecto: la tarea devuelve un dataclass `AgentRun(text, tool_calls=[(name, args)])` y tus evaluadores leen `ctx.output`.
- `LLMJudge(rubric=..., model=..., include_input=True)`: si no pasas `model`, usa `openai:gpt-5.2`. Fija siempre el modelo (`model=` o `set_default_judge_model`).

## Sin red por defecto

1. La `task` recibe una **fuente de respuestas inyectada**: un fake escrito a mano (guion de llamadas a tools y texto final por Case) o respuestas grabadas en JSON versionado. Frontera = el LLM; el servidor MCP corre real en memoria con los mocks de `mcp_server` (`create_server(app, simulator, base)`) para validar los argumentos contra el esquema real. Nada de `unittest.mock` sobre lógica propia.
2. El pytest offline comprueba el **dataset y los evaluadores**: casos buenos pasan, casos malos (respuesta que diagnostica, tool inventada, cifra inventada) fallan. Sin eso un evaluador roto da verde falso.
3. Si usas `pydantic_ai`, bloquea la red en el test: `pydantic_ai.models.ALLOW_MODEL_REQUESTS = False` (o `override_allow_model_requests(False)`), y modelos `TestModel`/`FunctionModel` de `pydantic_ai.models`.
4. `LLMJudge` nunca corre offline: un juez falso no prueba nada. El offline usa solo evaluadores deterministas.

## Dónde viven y markers

- Evaluadores, datasets y runner: `spine_monitor/agent/evals/` (fuera de `agent/src`: `pydantic-evals` es dev y no debe llegar a producción; fuera de `testpaths`, cobertura y pyright).
- Tests offline: `spine_monitor/agent/tests/test_evals_*.py`, con los markers existentes: `unit` (por defecto) para evaluadores y fakes; `golden` si comparas con una grabación o snapshot versionado. **No se añade marker nuevo.**
- Evals con modelo real: opt-in manual, **fuera de pytest y del gate/CI**: `agent/evals/run_live.py` (script), que aborta salvo `SPINE_AGENT_EVALS_LIVE=1` y lleva el modelo en otra variable de entorno. Solo si hace falta correrlo con pytest, test `integration` con `skipif` por esa variable. Nunca en `GATE_MUTATION` ni en workflows.
- Comando offline: `cd spine_monitor && uv run pytest agent -q -p no:randomly`. Gate completo: el de `tdd-workflow` (cobertura ≥ 90 %, mutación sobre lo cambiado).
- Datasets como YAML/JSON (`Dataset.from_file` / `to_file`) o en Python; versiónalos con el código. Sin datos reales de astronautas: usa los ejemplos de `contracts/examples/valid/`.

## Cambiar el prompt sin romper evals

1. Corre los evals offline antes: línea base verde.
2. Escribe primero el Case del comportamiento nuevo o roto; velo en rojo (`tdd-workflow`).
3. Cambia el prompt. Un solo cambio por commit, para atribuir regresiones.
4. Offline en verde y, con Uri, un run en vivo manual: compara `report.averages()` antes y después; no bajes el listón de un evaluador para que pase.
5. Cambiar de modelo o parámetros cuenta como cambio de prompt. Si cambia una tool MCP (nombre, parámetros), actualiza antes los Cases.

## Señales de alarma — PARA

| Pensamiento | Realidad |
|---|---|
| «Pruebo con el modelo real, es lo que importa» | Cuesta dinero y no es determinista. Offline en el gate; vivo solo manual y opt-in. |
| «Lo meto en CI, es barato» | Los evals vivos nunca van al gate ni a CI. |
| «Dejo `LLMJudge` sin `model`» | Usa `openai:gpt-5.2` por defecto: red y coste inesperados. |
| «Uso `ToolCorrectness` y listo» | Necesita spans OTel de pydantic-ai; con otro runtime falla siempre. |
| «Todos los evals pasan a la primera» | Añade un caso que debe fallar; un evaluador que nunca falló no prueba nada. |
| «Relajo la frase prohibida para que pase» | Cambia el prompt, no el evaluador, salvo decisión de Uri. |
| «Pongo el evaluador en `agent/src`» | `pydantic-evals` es dev; vive en `agent/evals/`. |
| «Añado un marker `eval`» | `conftest.py` exige un marker de tipo; usa `unit`/`golden`/`integration`. |
