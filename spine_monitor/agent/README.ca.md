[English](README.md) · [Español](README.es.md) · **Català**

# spine-agent

Agent LLM local que respon preguntes sobre l'estat de salut de la columna d'un astronauta fent servir les eines del servidor MCP (`spine_monitor/mcp_server`). Funciona sense connexió amb un model local d'[Ollama](https://ollama.com), respon en streaming (token a token) i en l'idioma de la pregunta (anglès, castellà o català).

Regles que compleix, per disseny:

- **Només lectura.** No pot canviar dades ni el semàfor, ni enviar informes; rebutja aquestes peticions sense cridar cap eina.
- **No diagnostica.** Ni diagnòstic mèdic ni consells de tractament; les preguntes clíniques es deriven al cirurgià de vol.
- **Diu «no ho sé»** quan una eina no retorna dades o dona error. Mai s'inventa xifres (un valor absent és «no disponible», mai 0).
- **Si falla el LLM, no falla res més.** L'agent és un procés apart: el camí directe app-motor no en depèn. Si Ollama està caigut, esgota el temps o no respon res, l'agent imprimeix un resum determinista construït amb les eines MCP (sense LLM).

Aquestes regles s'apliquen en tres capes: el prompt del sistema, un postfiltre en codi (`guards.py`) que afegeix un avís de correcció si una resposta afirma un diagnòstic, i els evals d'aquí sota.

## Com funciona

```
spine-agent (CLI, xat en streaming)
  └─ pydantic-ai Agent ── OllamaModel ──► Ollama (API compatible amb OpenAI, http://localhost:11434/v1)
        └─ toolset MCP (stdio) ──► python -m spine_mcp_server   (7 eines de només lectura)
  └─ si falla el model: McpStatusSource ──► get_current_status + explain_assessment ──► plantilla
```

El mode «thinking» està desactivat (trenca el tool calling de Qwen 3.5 a Ollama), la temperatura és 0,2 i Ollama corre amb context de 16k (el valor per defecte, 4k, trunca en silenci els esquemes de les eines).

## Inici ràpid

Necessita Docker. Ollama corre a Docker sota el perfil `llm` de compose i els models **es guarden en memòria cau al volum `ollama-models`**: es descarreguen una sola vegada i els arrencs següents no necessiten xarxa.

```bash
# Des de l'arrel del repositori. La primera vegada descarrega qwen3.5:4b (3,3 GB) al volum.
docker compose -f app/docker-compose.yml --profile llm up -d

# Xat (streaming, multitorn). Sortir amb /exit, /quit, Ctrl-D o Ctrl-C.
cd spine_monitor
uv run spine-agent --astronaut AST-01
```

`AST-01` és l'astronauta simulat que està en ambre; prova amb `Per què estic en ambre?`. `--astronaut` és obligatori. `--model` canvia el model.

- `ollama-pull` descarrega només els models de `SPINE_AGENT_LLM_MODELS` (etiquetes separades per espais, per defecte `qwen3.5:4b`) que manquin al volum, així que repetir `up` és immediat. Compose llegeix `.env` des d'`app/`: copia `.env.example` a `app/.env` per canviar-ho (l'exemple llista els tres models del benchmark, uns 16 GB).
- `docker compose ... down` conserva els models; `down -v` esborra el volum i obliga a descarregar de nou.
- Ollama escolta només a `127.0.0.1:11434` i està fixat a la versió 0.40.14.

### Configuració (variables d'entorn `SPINE_AGENT_*` o `.env`)

| Variable | Per defecte | Significat |
|---|---|---|
| `SPINE_AGENT_OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Endpoint d'Ollama compatible amb OpenAI |
| `SPINE_AGENT_MODEL` | `qwen3.5:4b` | Etiqueta del model |
| `SPINE_AGENT_TEMPERATURE` | `0.2` | Entre 0 i 2 |
| `SPINE_AGENT_REQUEST_TIMEOUT_S` | `60` | Temps màxim de la petició al model, en segons |
| `SPINE_AGENT_MCP_COMMAND` / `SPINE_AGENT_MCP_ARGS` | aquest Python / `["-m","spine_mcp_server"]` | Com es llança el servidor MCP (args en JSON) |
| `SPINE_AGENT_MCP_LOG_FILE` | cap | On va el log del servidor MCP; es descarta per defecte perquè no es barregi amb el xat |

## Evals

- **Offline (part del quality gate):** 28 casos (16 en anglès, 6 en castellà, 6 en català) a `evals/dataset.py`, executats amb un model fals guionitzat contra el servidor MCP real. A més de comprovar que les respostes bones passen, hi ha tests que donen respostes dolentes a propòsit per demostrar que cada avaluador falla quan toca. S'executen amb `uv run pytest agent`.
- **Live (mai a CI):** mesura models reals d'Ollama amb els mateixos casos i els mateixos avaluadors. Mesura el temps fins al primer token, la latència total i els tokens/s, i imprimeix les respostes que fallen.

```bash
cd spine_monitor/agent
SPINE_AGENT_EVALS_LIVE=1 uv run --no-sync python -m evals.run_live \
    --models qwen3.5:4b gemma4:e4b qwen3.5:9b --repeat 1 --dump failures.jsonl
```

## Nota del model

Mesurat el 2026-11-06 amb Ollama 0.40.14 **a Docker, només CPU** (Intel Core Ultra 7 155H, 32 GB de RAM, VM de Docker amb 16 GB; sense GPU dedicada), 28 casos, **una repetició**.

| Model | Mida (Q4_K_M) | Encert | Primer token (mediana / p90) | Latència total (mediana) | Tokens/s |
|---|---|---|---|---|---|
| **`qwen3.5:4b`** (triat) | 3,3 GB | **82 %** (23/28) | 24,0 s / 67,4 s | 29,9 s | 2,1 |
| `gemma4:e4b` | 6,6 GB | 50 % (14/28) | 0,8 s / 51,5 s | 3,6 s | 6,8 |
| `qwen3.5:9b` | 6,6 GB | 68 % (19/28) | 40,0 s / 118,2 s | 51,6 s | 1,3 |

Els tokens/s són tokens de sortida entre el temps dins de l'stream del model, així que inclouen el processament del prompt; per això són baixos a CPU.

**Per què `qwen3.5:4b`:** té el millor encert i gestiona bé els rebutjos (trampes de diagnòstic 6/6, peticions d'escriptura 4/4). `gemma4:e4b` és el més ràpid, però els seus 0,8 s enganyen: en 10 de les seves 14 execucions fallides no va cridar cap eina i va respondre sense llegir dades, que és just el que aquest agent no ha de fer mai. `qwen3.5:9b` és més lent i va sortir pitjor aquí (simulació 0/4).

### Límits

- **Latència a CPU.** El model triat necessita una mediana de 24 s (p90 67 s) per començar a respondre en aquest portàtil. L'objectiu de 15 s que ens vam posar no el va complir cap model que a més llegís les dades. Una GPU NVIDIA dedicada o un Mac amb Apple Silicon haurien de ser molt més ràpids, però no es va mesurar. Ollama a Docker sobre Windows no fa servir la iGPU d'Intel.
- **Mostra petita.** Una repetició de 28 casos; els resultats canvien entre execucions (el mateix model va passar del 57 % al 82 % quan vam arreglar els nostres propis avaluadors). Pren les xifres com un ordre de magnitud.
- **Un model de 4B de vegades falla.** A les execucions fallides es va saltar una eina que havia de cridar (una pregunta en català, una simulació en castellà) o va presentar un escenari sense executar-lo. El fallback només cobreix un model no disponible, no un que respon malament.
- **Simulacions.** `simulate_scenario` exigeix almenys un override, així que «i si mantinc aquesta càrrega?» (sense canvis) no es pot expressar i el model sol demanar-ne un a l'usuari. És un límit de l'eina MCP.
- **Sense data actual.** El prompt no inclou la data, així que preguntes com «com ha canviat el meu estat aquesta setmana?» no es poden respondre (`get_history` necessita dates absolutes).
- **Comprovacions per paraules clau.** El postfiltre de diagnòstics i diversos avaluadors fan servir llistes de paraules en anglès, castellà i català: no detecten formulacions que no siguin a les llistes, i una afirmació només es pot corregir després d'haver-se mostrat.
- **El català és més fluix** que l'anglès i el castellà en els models petits (una execució va entendre «Mart» com una hora del dia).
- **Encara no hi ha ambre del motor real.** El triatge del motor continua sent un stub que sempre retorna verd, així que «per què estic en ambre?» només funciona amb les dades simulades.
