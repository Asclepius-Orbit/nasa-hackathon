[English](README.md) · **Español** · [Català](README.ca.md)

# spine-agent

Agente LLM local que responde preguntas sobre el estado de salud de la columna de un astronauta usando las herramientas del servidor MCP (`spine_monitor/mcp_server`). Funciona sin conexión con un modelo local de [Ollama](https://ollama.com), responde en streaming (token a token) y en el idioma de la pregunta (inglés, castellano o catalán).

Reglas que cumple, por diseño:

- **Solo lectura.** No puede cambiar datos ni el semáforo, ni enviar informes; rechaza esas peticiones sin llamar a ninguna tool.
- **No diagnostica.** Ni diagnóstico médico ni consejos de tratamiento; las preguntas clínicas se derivan al cirujano de vuelo.
- **Dice «no lo sé»** cuando una tool no devuelve datos o da error. Nunca inventa cifras (un valor ausente es «no disponible», nunca 0).
- **Si falla el LLM, no falla nada más.** El agente es un proceso aparte: el camino directo app-motor no depende de él. Si Ollama está caído, agota el tiempo o no responde nada, el agente imprime un resumen determinista construido con las tools MCP (sin LLM).

Estas reglas se aplican en tres capas: el prompt del sistema, un post-filtro en código (`guards.py`) que añade un aviso de corrección si una respuesta afirma un diagnóstico, y los evals de abajo.

## Cómo funciona

```
spine-agent (CLI, chat en streaming)
  └─ pydantic-ai Agent ── OllamaModel ──► Ollama (API compatible con OpenAI, http://localhost:11434/v1)
        └─ toolset MCP (stdio) ──► python -m spine_mcp_server   (7 tools de solo lectura)
  └─ si falla el modelo: McpStatusSource ──► get_current_status + explain_assessment ──► plantilla
```

El modo «thinking» está desactivado (rompe el tool calling de Qwen 3.5 en Ollama), la temperatura es 0,2 y Ollama corre con contexto de 16k (su valor por defecto, 4k, trunca en silencio los esquemas de las tools).

## Inicio rápido

Necesita Docker. Ollama corre en Docker bajo el perfil `llm` de compose y los modelos **se guardan en caché en el volumen `ollama-models`**: se descargan una sola vez y los arranques siguientes no necesitan red.

```bash
# Desde la raíz del repositorio. La primera vez descarga qwen3.5:4b (3,3 GB) al volumen.
docker compose -f app/docker-compose.yml --profile llm up -d

# Chat (streaming, multi-turno). Salir con /exit, /quit, Ctrl-D o Ctrl-C.
cd spine_monitor
uv run spine-agent --astronaut AST-01
```

`AST-01` es el astronauta simulado que está en ámbar; prueba con `¿Por qué estoy en ámbar?`. `--astronaut` es obligatorio. `--model` cambia el modelo.

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
| `SPINE_AGENT_MCP_COMMAND` / `SPINE_AGENT_MCP_ARGS` | este Python / `["-m","spine_mcp_server"]` | Cómo se lanza el servidor MCP (args en JSON) |
| `SPINE_AGENT_MCP_LOG_FILE` | ninguno | Dónde va el log del servidor MCP; se descarta por defecto para que no se mezcle con el chat |

## Evals

- **Offline (parte del quality gate):** 28 casos (16 en inglés, 6 en castellano, 6 en catalán) en `evals/dataset.py`, ejecutados con un modelo falso guionizado contra el servidor MCP real. Además de comprobar que las respuestas buenas pasan, hay tests que dan respuestas malas a propósito para demostrar que cada evaluador falla cuando debe. Se ejecutan con `uv run pytest agent`.
- **Live (nunca en CI):** mide modelos reales de Ollama con los mismos casos y los mismos evaluadores. Mide tiempo hasta el primer token, latencia total y tokens/s, e imprime las respuestas que fallan.

```bash
cd spine_monitor/agent
SPINE_AGENT_EVALS_LIVE=1 uv run --no-sync python -m evals.run_live \
    --models qwen3.5:4b gemma4:e4b qwen3.5:9b --repeat 1 --dump failures.jsonl
```

## Nota del modelo

Medido el 2026-11-06 con Ollama 0.40.14 **en Docker, solo CPU** (Intel Core Ultra 7 155H, 32 GB de RAM, VM de Docker con 16 GB; sin GPU dedicada), 28 casos, **una repetición**.

| Modelo | Tamaño (Q4_K_M) | Acierto | Primer token (mediana / p90) | Latencia total (mediana) | Tokens/s |
|---|---|---|---|---|---|
| **`qwen3.5:4b`** (elegido) | 3,3 GB | **82 %** (23/28) | 24,0 s / 67,4 s | 29,9 s | 2,1 |
| `gemma4:e4b` | 6,6 GB | 50 % (14/28) | 0,8 s / 51,5 s | 3,6 s | 6,8 |
| `qwen3.5:9b` | 6,6 GB | 68 % (19/28) | 40,0 s / 118,2 s | 51,6 s | 1,3 |

Los tokens/s son tokens de salida entre el tiempo dentro del stream del modelo, así que incluyen el procesamiento del prompt; por eso son bajos en CPU.

**Por qué `qwen3.5:4b`:** tiene el mejor acierto y gestiona bien los rechazos (trampas de diagnóstico 6/6, peticiones de escritura 4/4). `gemma4:e4b` es el más rápido, pero sus 0,8 s engañan: en 10 de sus 14 ejecuciones fallidas no llamó a ninguna tool y respondió sin leer datos, que es justo lo que este agente nunca debe hacer. `qwen3.5:9b` es más lento y salió peor aquí (simulación 0/4).

### Límites

- **Latencia en CPU.** El modelo elegido necesita una mediana de 24 s (p90 67 s) para empezar a responder en este portátil. El objetivo de 15 s que nos pusimos no lo cumplió ningún modelo que además leyera los datos. Una GPU NVIDIA dedicada o un Mac con Apple Silicon deberían ser mucho más rápidos, pero no se midió. Ollama en Docker sobre Windows no usa la iGPU de Intel.
- **Muestra pequeña.** Una repetición de 28 casos; los resultados cambian entre ejecuciones (el mismo modelo pasó del 57 % al 82 % cuando arreglamos nuestros propios evaluadores). Toma las cifras como un orden de magnitud.
- **Un modelo de 4B a veces falla.** En las ejecuciones fallidas se saltó una tool que debía llamar (una pregunta en catalán, una simulación en castellano) o presentó un escenario sin ejecutarlo. El fallback solo cubre un modelo no disponible, no uno que responde mal.
- **Simulaciones.** `simulate_scenario` exige al menos un override, así que «¿y si mantengo esta carga?» (sin cambios) no se puede expresar y el modelo suele pedirle uno al usuario. Es un límite de la tool MCP.
- **Sin fecha actual.** El prompt no incluye la fecha, así que preguntas como «¿cómo ha cambiado mi estado esta semana?» no se pueden responder (`get_history` necesita fechas absolutas).
- **Comprobaciones por palabras clave.** El post-filtro de diagnósticos y varios evaluadores usan listas de palabras en inglés, castellano y catalán: no detectan formulaciones que no estén en las listas, y una afirmación solo se puede corregir después de haberse mostrado.
- **El catalán es más flojo** que el inglés y el castellano en los modelos pequeños (una ejecución entendió «Mart» como una hora del día).
- **Aún no hay ámbar del motor real.** El triaje del motor sigue siendo un stub que siempre devuelve verde, así que «¿por qué estoy en ámbar?» solo funciona con los datos simulados.
