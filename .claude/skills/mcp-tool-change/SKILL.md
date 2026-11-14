---
name: mcp-tool-change
description: Use when se va a añadir, renombrar o cambiar parámetros, salida o errores de una tool MCP en `spine_monitor/mcp_server`, antes de tocar `server.py`.
---

# mcp-tool-change

Cambiar una tool es cambiar un contrato público que lee un LLM pequeño local. Todo cambio de código sigue `tdd-workflow` (rojo visible primero). Para el diseño de la tool (nombre, descripción, paginación, errores accionables) lee `mcp-builder`; si choca con este repo, manda el repo.

Ficheros (desde `spine_monitor/mcp_server/`): `src/spine_mcp_server/{server,ports,mocks,compare,errors}.py`, `tests/{test_server,test_ports_mocks,test_compare,test_stdio}.py`, `README.md`, `README.es.md`, `README.ca.md`.

## Pasos

1. **Decide la tool.** Nombre `verbo_objeto` en snake_case e inglés, como `get_history`. Parámetros en inglés, cada uno con `Field(description=...)`; ids con `AstronautId`; ventanas con `AwareDatetime` y `_window(...)`. Si no puede ser solo lectura o simulación, PARA y pregunta a Uri: el servidor nunca escribe datos ni diagnostica.
2. **Anotaciones y descripción.** `annotations=_READ_ONLY`. La descripción dice qué devuelve, que los datos pueden ser `null` o faltar, y que nunca diagnostica (un test lo exige).
3. **Salida: contrato primero.** Si la tool devuelve un esquema de `contracts/schemas/v1/` nuevo o cambiado, usa la skill `contract-first` (esquema, ejemplo en `contracts/examples/valid/`, regenerar modelos) antes de seguir. Si reutiliza uno existente, no lo toques.
4. **Puerto.** Si necesita datos nuevos, añade el método al Protocol de `ports.py` (`AppReader`, `Simulator` o `AssessBaseSource`) con docstring y solo lectura. Si la tool se puede calcular con puertos existentes, hazlo (como `compare_periods`, que llama a `get_history` dos veces) y pon la lógica pura en un módulo sin I/O.
5. **Mock.** Implementa el método en `mocks.py` sirviendo el ejemplo válido (`EXAMPLES_DIR`); filtra, ordena de más antiguo a más reciente y calcula `truncated` si hay ventana y `limit`. Errores como `UpstreamError(404, ...)` o `UpstreamError(422, "invalid_range", ...)`.
6. **Límites.** Listas con `limit` (1 a 500, por defecto 100, `MAX_HISTORY_LIMIT`), `truncated` y las más antiguas descartadas. Nada de volcados sin tope.
7. **Errores.** Dentro de `with _upstream_as_tool_errors():` llama al puerto; un `UpstreamError` sale como `isError: true` con `título: detalle`. Un fallo de lógica propia lanza `ToolError(str(error))`. No dejes escapar otras excepciones.
8. **Registro.** `@server.tool(name=..., annotations=_READ_ONLY, description=...)` en `create_server`; retorno `Annotated[CallToolResult, Modelo]` y `_result(modelo)` (usa `dump`, no `model_dump`).
9. **Recuento.** Si cambia el número de tools, busca `7` y `seven` y corrígelos: docstrings de `server.py`, `tests/test_server.py` (`TOOLS`, `test_lists_exactly_the_seven_tools`, `test_main_serves_the_seven_tools_over_stdio`) y `tests/test_stdio.py`, y la frase de apertura de los tres READMEs («7 read-only and simulation tools»...).
10. **READMEs.** En `README.md`, `README.es.md` y `README.ca.md`: fila de la tabla de tools (parámetros y esquema de salida), notas de la tool y el recuento. Los tres sincronizados, con el enlace de cambio de idioma de la primera línea intacto. Sin referencias al vault de Obsidian. Llama a Uri «Uri» en cualquier texto al equipo.

## Tests (rojo primero)

Patrón en memoria de `tests/test_server.py`: `_server(app, simulator, base)` construye `create_server` con dobles escritos a mano (`RecordingApp` apunta las llamadas; `FailingApp` lanza `UpstreamError(503, "Boom", ...)`); `_call`/`_tools` usan `Client(server)` dentro de `@pytest.mark.anyio`. Escribe antes que el código:

- la tool aparece en `TOOLS` y cumple los tests transversales (solo lectura, parámetros descritos, esquema de salida);
- una fila en `CALLS` (marker `contract`): la salida valida con `validate("<esquema>", ...)`;
- el puerto recibe los argumentos esperados y el `limit` por defecto y sus límites;
- un fallo del puerto da `is_error` con `Boom: it broke`; los casos límite (vacío, `null`, truncado);
- el mock en `tests/test_ports_mocks.py` (404, 422, orden, `truncated`).

Un marker por test (`unit`, `integration` o `contract`). Gate de `tdd-workflow` al final.

## Verificación manual (MCP Inspector)

Desde `spine_monitor/`, ligado a localhost:

```bash
pnpm dlx @modelcontextprotocol/inspector@2.14.0 uv run spine-mcp-server
```

Comprueba que la tool aparece, su esquema de entrada y salida, una llamada válida y una inválida (`isError`).

## Señales de alarma — PARA

| Pensamiento | Realidad |
|---|---|
| «Añado una tool que guarda o corrige datos» | El servidor es solo lectura y simulación. Pregunta a Uri. |
| «La descripción puede decir "diagnostica"/«detecta lesión»» | Describe evidencia y niveles de riesgo, nunca diagnóstico. |
| «Devuelvo 0 si falta el dato» | Falta = `null`, nunca 0. |
| «Edito el JSON Schema o `models/` a mano» | Usa `contract-first`; los modelos se regeneran. |
| «Llamo a la API de la app desde `server.py`» | Solo a través de un puerto de `ports.py`. |
| «Devuelvo todos los registros» | `limit` 1 a 500 y `truncated`. |
| «Actualizo solo el README en inglés» | Los tres READMEs a la vez, y el recuento de tools. |
| «Pruebo con el Inspector y me salto el test» | Sin test en rojo primero no hay tool. |
