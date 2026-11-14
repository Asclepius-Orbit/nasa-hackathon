[English](README.md) · **Español** · [Català](README.ca.md)

# spine-agent

Agente LLM local que responde preguntas sobre el estado de salud de la columna de un astronauta usando las herramientas del servidor MCP (`spine_monitor/mcp_server`). Funciona sin conexión con un modelo local de [Ollama](https://ollama.com), responde en streaming (token a token) y en el idioma de la pregunta (inglés, castellano o catalán).

Reglas que cumple, por diseño:

- **Solo lectura.** No puede cambiar datos ni el semáforo, ni enviar informes; rechaza esas peticiones sin llamar a ninguna tool.
- **No diagnostica.** Ni diagnóstico médico ni consejos de tratamiento; las preguntas clínicas se derivan al cirujano de vuelo.
- **Dice «no lo sé»** cuando una tool no devuelve datos o da error. Nunca inventa cifras (un valor ausente es «no disponible», nunca 0), y el prompt le pide citar los valores que devuelven las tools en vez de calcular otros nuevos.
- **Si falla el LLM, no falla nada más.** El agente es un proceso aparte: el camino directo app-motor no depende de él. Si Ollama está caído, agota el tiempo o no responde nada, el agente imprime un resumen determinista construido con las tools MCP (sin LLM).

Estas reglas se aplican en tres capas: el prompt del sistema, un post-filtro en código (`guards.py`) que añade un aviso de corrección si una respuesta afirma un diagnóstico, y los evals de abajo.

## Cómo funciona

```
spine-agent (CLI, chat en streaming)
  └─ pydantic-ai Agent ── OllamaModel ──► Ollama (API compatible con OpenAI, http://localhost:11434/v1)
        └─ toolset MCP (stdio) ──► python -m spine_mcp_server   (7 tools de solo lectura)
  └─ si falla el modelo: McpStatusSource ──► get_current_status + explain_assessment ──► plantilla
```

- El modo «thinking» está desactivado (rompe el tool calling de Qwen 3.5 en Ollama), la temperatura es 0,2 y Ollama corre con contexto de 16k (su valor por defecto, 4k, trunca en silencio los esquemas de las tools).
- En cada turno las instrucciones llevan la fecha y hora actuales (UTC) y tres **rangos ya calculados** por código (últimas 24 horas, últimos 7 días, 7 días anteriores). Un modelo pequeño es malo con la aritmética de fechas, así que copia los rangos en `get_history` / `compare_periods` en vez de calcular fechas.
- El prompt le pide llamar a una tool antes de afirmar cualquier dato del astronauta («tool first»). Es solo una regla del prompt: nada en el código lo obliga.

## Inicio rápido

Necesita Docker. Ollama corre en Docker bajo el perfil `llm` de compose y los modelos **se guardan en caché en el volumen `ollama-models`**: se descargan una sola vez y los arranques siguientes no necesitan red.

```bash
# Desde la raíz del repositorio. Una vez: las credenciales del broker (compose las necesita).
sh app/mosquitto/setup.sh
# Prepara los modelos con conexi?n; el arranque normal no descarga modelos.
docker compose -f app/docker-compose.yml --profile llm-prepare run --rm ollama-pull
# Runtime:
docker compose -f app/docker-compose.yml --profile llm up -d

# Chat (streaming, multi-turno). Salir con /exit, /quit, Ctrl-D o Ctrl-C.
cd spine_monitor
uv run spine-agent --astronaut AST-01
```

Por defecto el agente lanza el servidor MCP con `--dataset http`, que lee el read-api de la app y el motor reales, así que ambos deben estar en marcha (`SPINE_APP_URL` y `SPINE_ENGINE_URL` se reenvían al subproceso MCP). `AST-01` es el astronauta de demo del servidor MCP (`--dataset demo`): sus datos se generan en relación con el reloj (14 días, en ámbar los últimos 6); para chatear contra él define `SPINE_AGENT_MCP_ARGS='["-m","spine_mcp_server","--dataset","demo"]'`. Prueba con `¿Por qué estoy en ámbar?`, `¿Cómo ha cambiado mi estado esta semana?` o `¿Qué pasaría si mantengo esta carga 30 días?`. `--astronaut` es obligatorio. `--model` cambia el modelo.

- `ollama-pull` descarga solo los modelos de `SPINE_AGENT_LLM_MODELS` (tags separados por espacios, por defecto `qwen3.5:4b`) que falten en el volumen, así que repetir `up` es inmediato. Compose lee `.env` desde `app/`: copia `.env.example` a `app/.env` para cambiarlo (el ejemplo lista los tres modelos del benchmark, unos 16 GB).
- `docker compose ... down` conserva los modelos; `down -v` borra el volumen y obliga a descargar de nuevo.
- Ollama escucha solo en `127.0.0.1:11434` y está fijado a la versión 0.40.14.

### Configuración (variables de entorno `SPINE_AGENT_*` o `.env`)

| Variable | Por defecto | Significado |
|---|---|---|
| `SPINE_AGENT_OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Endpoint de Ollama compatible con OpenAI |
| `SPINE_AGENT_MODEL` | `qwen3.5:4b` | Tag del modelo |
| `SPINE_AGENT_TEMPERATURE` | `0.2` | Entre 0 y 2 |
| `SPINE_AGENT_REQUEST_TIMEOUT_S` | `60` | Tiempo máximo de la petición al modelo, en segundos |
| `SPINE_AGENT_MCP_COMMAND` / `SPINE_AGENT_MCP_ARGS` | este Python / `["-m","spine_mcp_server","--dataset","http"]` | Cómo se lanza el servidor MCP (args en JSON); por defecto lee la app y el motor reales (deben estar en marcha; `SPINE_APP_URL` y `SPINE_ENGINE_URL` se reenvían); usa `"--dataset","demo"` para los datos de demo relativos al reloj (`AST-01`) o `"--dataset","examples"` para los ejemplos estáticos de los contratos |
| `SPINE_AGENT_MCP_LOG_FILE` | ninguno | Dónde va el log del servidor MCP; se descarta por defecto para que no se mezcle con el chat |
| `SPINE_AGENT_HTTP_HOST` / `SPINE_AGENT_HTTP_PORT` | `127.0.0.1` / `8003` | Dirección del servidor HTTP de chat |
| `SPINE_AGENT_HTTP_TOKEN` | ninguno | Token Bearer del servidor HTTP de chat, compartido con el proceso principal de Electron |
| `SPINE_AGENT_HTTP_TOKEN_FILE` | `~/.spine-agent/http-token` | De dónde se lee el token si `SPINE_AGENT_HTTP_TOKEN` no está definido; si falta, se crea con un token aleatorio (solo legible por el propietario) |
| `SPINE_AGENT_HTTP_MAX_CONVERSATIONS` | `16` | Conversaciones en memoria; si está lleno, se olvida la inactiva usada hace más tiempo |
| `SPINE_AGENT_HTTP_IDLE_TTL_S` | `3600` | Una conversación inactiva se olvida tras estos segundos |
| `SPINE_AGENT_HTTP_KEEPALIVE_S` | `15` | Se envía un comentario SSE `: ping` tras estos segundos sin eventos |

## Servidor HTTP de chat (para la app de escritorio)

La app de escritorio habla con el agente por HTTP local con eventos SSE (server-sent events). El contrato es [`contracts/agent-openapi.yaml`](../contracts/agent-openapi.yaml) con los schemas `contracts/schemas/v1/agent/chat-request.schema.json` y `chat-event.schema.json`.

```bash
cd spine_monitor
SPINE_AGENT_HTTP_TOKEN=cambialo uv run spine-agent-http     # http://127.0.0.1:8003

curl -N http://127.0.0.1:8003/chat \
  -H "Authorization: Bearer cambialo" -H "X-Astronaut-Id: AST-01" -H "Content-Type: application/json" \
  -d '{"schema_version":"1.0.0","astronaut_id":"AST-01","conversation_id":"c0ffee00-0000-4000-8000-000000000001","question":"¿Por qué estoy en ámbar?"}'
```

- `POST /chat` responde 200 con un `text/event-stream`. Cada evento es `event: <tipo>` más una línea `data:` con JSON: `text` (`delta`), `tool_call` (`tool_name`, `tool_args`), `fallback` (`text`), `notice` (`code` `mid_stream` o `correction`, `text`) y, al final, exactamente un `end` o un `error` (`code` `busy`, `unavailable`, `invalid` o `internal`, `message`). Los comentarios `: ping` mantienen viva la conexión mientras el modelo piensa (en CPU el primer token tarda unos 27 s, a veces un minuto).
- El cliente genera `conversation_id`: un id nuevo empieza una conversación y el mismo id la continúa. El historial vive solo en memoria, acotado y con TTL de inactividad; una conversación pertenece al astronauta de su primera pregunta.
- Una respuesta a la vez por conversación (una segunda pregunta mientras tanto es un 409). Para cancelar una respuesta se cierra la conexión: la ejecución del modelo se detiene y el turno no se guarda. `DELETE /chat/{conversation_id}` olvida una conversación (204, también si no existe).
- Toda petición salvo `/health` necesita `Authorization: Bearer <token>` y `X-Astronaut-Id` (el astronauta desbloqueado, igual al `astronaut_id` del cuerpo): 401, 403, 422 y 503 son problem details con el schema `app/error`.
- Sin LLM (Ollama caído o fallando) la respuesta sigue siendo un stream: un resumen `fallback` hecho con las tools MCP y luego `end`. Si el propio servidor MCP no puede arrancar, el stream termina con `error` `unavailable`.
- El servidor arranca el servidor MCP una vez (subproceso stdio) y lo para al apagarse. Hay una imagen Docker en `agent/Dockerfile` (escucha en `0.0.0.0:8003` dentro del contenedor y guarda el token generado en `/data`).

## Evals

- **Offline (parte del quality gate):** 32 casos (18 en inglés, 7 en castellano, 7 en catalán) en `evals/dataset.py`, ejecutados con un modelo falso guionizado contra el servidor MCP real sobre el dataset de demo con reloj fijo. Categorías: explicación del ámbar, sin datos, trampas de diagnóstico, peticiones de escritura, simulaciones, tendencias del histórico y preguntas fuera de alcance. Los evaluadores comprueban además que las cifras de la respuesta coinciden con un resultado de una tool (`NumbersGrounded`, sin importar el signo), que `get_history` y `compare_periods` usan exactamente los rangos ya calculados (`RangesMatch`, en los casos de histórico) y que en el caso de comparar semanas el periodo A es esta semana (`ComparisonDirection`, solo argumentos). Además de comprobar que las respuestas buenas pasan, hay tests que dan respuestas malas a propósito para demostrar que cada evaluador falla cuando debe. Se ejecutan con `uv run pytest agent`.
- **Live (nunca en CI):** mide modelos reales de Ollama con los mismos casos y los mismos evaluadores. Mide tiempo hasta el primer token, latencia total y tokens/s, e imprime las respuestas que fallan.

```bash
cd spine_monitor/agent
SPINE_AGENT_EVALS_LIVE=1 uv run --no-sync python -m evals.run_live \
    --models qwen3.5:4b gemma4:e4b qwen3.5:9b --repeat 1 --dump failures.jsonl
```

## Nota del modelo

Medido el 2026-11-06 con Ollama 0.40.14 **en Docker, solo CPU** (Intel Core Ultra 7 155H, 32 GB de RAM, VM de Docker con 16 GB; sin GPU dedicada), **una repetición** por modelo.

| Modelo | Tamaño (Q4_K_M) | Acierto | Primer token (mediana / p90) | Latencia total (mediana) | Tokens/s | Casos |
|---|---|---|---|---|---|---|
| **`qwen3.5:4b`** (elegido) | 3,3 GB | **81 %** (26/32) | 27,2 s / 60,7 s | 30,4 s | 2,1 | 32, prompt final |
| `gemma4:e4b` | 6,6 GB | 50 % (14/28) | 0,8 s / 51,5 s | 3,6 s | 6,8 | 28, primer prompt |
| `qwen3.5:9b` | 6,6 GB | 68 % (19/28) | 40,0 s / 118,2 s | 51,6 s | 1,3 | 28, primer prompt |

Los tokens/s son tokens de salida entre el tiempo dentro del stream del modelo, así que incluyen el procesamiento del prompt; por eso son bajos en CPU. **Las tres filas no son estrictamente comparables**: `gemma4:e4b` y `qwen3.5:9b` se midieron primero, con el primer prompt y 28 casos, y no se repitieron tras cambiar el prompt, el manejo de fechas y el dataset de demo (solo se volvió a medir el modelo elegido). Todas las filas se midieron con el dataset de demo; una ejecución contra el motor real sigue pendiente.

**Por qué `qwen3.5:4b`:** tiene el mejor acierto y gestiona bien los rechazos (trampas de diagnóstico 6/6, peticiones de escritura 4/4). `gemma4:e4b` es el más rápido, pero sus 0,8 s engañan: en 10 de sus 14 ejecuciones fallidas no llamó a ninguna tool y respondió sin leer datos, que es justo lo que este agente nunca debe hacer. `qwen3.5:9b` es más lento y salió peor (simulación 0/4).

**El resultado del 4B cambia mucho entre ejecuciones.** El mismo modelo sacó 82 % (primer prompt, 28 casos), 91 % (prompt «tool first», fecha actual, 32 casos) y 81 % (rangos ya calculados y «no calcules números», 32 casos); con una repetición cuenta con unos ±10 puntos. En la última ejecución `simulation` (3/4; ahora funciona con `overrides` vacío), `amber_explanation` (5/5), `diagnosis_trap` (6/6), `write_request` (4/4) y `no_data` (5/5) estuvieron sólidas. `history_trend` fue 0/4: dos de esos cuatro eran un evaluador que exigía `get_history` cuando el modelo había respondido bien con `compare_periods` (corregido después, así que la tabla de arriba los sigue contando como fallos), y los otros dos eran reales: el modelo calculó diferencias por su cuenta y, en «compara esta semana con la anterior», intercambió qué semana era cuál.

### Límites

- **Latencia en CPU.** El modelo elegido necesita una mediana de 27 s (p90 61 s) para empezar a responder en este portátil; las preguntas de histórico son las más lentas porque sus resultados de tool son grandes y hay que procesarlos en CPU. El objetivo de 15 s que nos pusimos no lo cumplió ningún modelo que además leyera los datos. Una GPU NVIDIA dedicada o un Mac con Apple Silicon deberían ser mucho más rápidos, pero no se midió. Ollama en Docker sobre Windows no usa la iGPU de Intel.
- **Muestra pequeña.** Una repetición de 32 casos; los resultados cambian entre ejecuciones en unos ±10 puntos. Toma las cifras como un orden de magnitud.
- **Un modelo de 4B a veces falla.** Puede saltarse una tool que debía llamar, presentar una simulación sin decir que es hipotética, calcular números que ninguna tool devolvió o intercambiar los dos periodos de una comparación. El fallback solo cubre un modelo no disponible, no uno que responde mal, y la regla «tool first» no la impone el código.
- **Comprobaciones por palabras clave.** El post-filtro de diagnósticos y varios evaluadores usan listas de palabras en inglés, castellano y catalán: no detectan formulaciones que no estén en las listas, y una afirmación solo se puede corregir después de haberse mostrado.
- **El catalán es más flojo** que el inglés y el castellano en los modelos pequeños (una ejecución entendió «Mart» como una hora del día y otra mezcló «ha millorat» con un empeoramiento).
- **Medido con datos de demo, aún no con el motor real.** Los evals offline y la tabla de modelos usan el dataset de demo (reloj fijo, y allí `simulate_scenario` es una aproximación lineal simple). El motor ya ejecuta el pipeline por defecto, pero su política de triaje es declarativa y provisional (el catálogo clínico está pendiente de revisión), y los evals no se han vuelto a ejecutar contra él.
