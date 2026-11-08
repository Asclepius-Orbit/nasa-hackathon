[English](README.md) · [Español](README.es.md) · **Català**

# spine-agent

Agent LLM local que respon preguntes sobre l'estat de salut de la columna d'un astronauta fent servir les eines del servidor MCP (`spine_monitor/mcp_server`). Funciona sense connexió amb un model local d'[Ollama](https://ollama.com), respon en streaming (token a token) i en l'idioma de la pregunta (anglès, castellà o català).

Regles que compleix, per disseny:

- **Només lectura.** No pot canviar dades ni el semàfor, ni enviar informes; rebutja aquestes peticions sense cridar cap eina.
- **No diagnostica.** Ni diagnòstic mèdic ni consells de tractament; les preguntes clíniques es deriven al cirurgià de vol.
- **Diu «no ho sé»** quan una eina no retorna dades o dona error. Mai s'inventa xifres (un valor absent és «no disponible», mai 0), i el prompt li demana citar els valors que retornen les eines en lloc de calcular-ne de nous.
- **Si falla el LLM, no falla res més.** L'agent és un procés apart: el camí directe app-motor no en depèn. Si Ollama està caigut, esgota el temps o no respon res, l'agent imprimeix un resum determinista construït amb les eines MCP (sense LLM).

Aquestes regles s'apliquen en tres capes: el prompt del sistema, un postfiltre en codi (`guards.py`) que afegeix un avís de correcció si una resposta afirma un diagnòstic, i els evals d'aquí sota.

## Com funciona

```
spine-agent (CLI, xat en streaming)
  └─ pydantic-ai Agent ── OllamaModel ──► Ollama (API compatible amb OpenAI, http://localhost:11434/v1)
        └─ toolset MCP (stdio) ──► python -m spine_mcp_server   (7 eines de només lectura)
  └─ si falla el model: McpStatusSource ──► get_current_status + explain_assessment ──► plantilla
```

- El mode «thinking» està desactivat (trenca el tool calling de Qwen 3.5 a Ollama), la temperatura és 0,2 i Ollama corre amb context de 16k (el valor per defecte, 4k, trunca en silenci els esquemes de les eines).
- A cada torn les instruccions porten la data i l'hora actuals (UTC) i tres **rangs ja calculats** per codi (darreres 24 hores, darrers 7 dies, 7 dies anteriors). Un model petit és dolent amb l'aritmètica de dates, així que copia els rangs a `get_history` / `compare_periods` en lloc de calcular dates.
- El prompt li demana cridar una eina abans d'afirmar qualsevol dada de l'astronauta («tool first»). És només una regla del prompt: res al codi ho obliga.

## Inici ràpid

Necessita Docker. Ollama corre a Docker sota el perfil `llm` de compose i els models **es guarden en memòria cau al volum `ollama-models`**: es descarreguen una sola vegada i els arrencs següents no necessiten xarxa.

```bash
# Des de l'arrel del repositori. La primera vegada descarrega qwen3.5:4b (3,3 GB) al volum.
docker compose -f app/docker-compose.yml --profile llm up -d

# Xat (streaming, multitorn). Sortir amb /exit, /quit, Ctrl-D o Ctrl-C.
cd spine_monitor
uv run spine-agent --astronaut AST-01
```

`AST-01` és l'astronauta de demo del servidor MCP: les seves dades es generen en relació amb el rellotge (14 dies, en ambre els últims 6). Prova amb `Per què estic en ambre?`, `Com ha canviat el meu estat aquesta setmana?` o `Què passaria si mantinc aquesta càrrega 30 dies?`. `--astronaut` és obligatori. `--model` canvia el model.

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
| `SPINE_AGENT_MCP_COMMAND` / `SPINE_AGENT_MCP_ARGS` | aquest Python / `["-m","spine_mcp_server"]` | Com es llança el servidor MCP (args en JSON); afegeix `"--dataset","examples"` per als exemples estàtics dels contractes |
| `SPINE_AGENT_MCP_LOG_FILE` | cap | On va el log del servidor MCP; es descarta per defecte perquè no es barregi amb el xat |

## Evals

- **Offline (part del quality gate):** 32 casos (18 en anglès, 7 en castellà, 7 en català) a `evals/dataset.py`, executats amb un model fals guionitzat contra el servidor MCP real sobre el dataset de demo amb rellotge fix. Categories: explicació de l'ambre, sense dades, trampes de diagnòstic, peticions d'escriptura, simulacions, tendències de l'històric i preguntes fora d'abast. Els avaluadors també comproven que les xifres de la resposta coincideixen amb un resultat d'una eina (`NumbersGrounded`, sense importar el signe), que `get_history` i `compare_periods` fan servir exactament els rangs ja calculats (`RangesMatch`, als casos d'històric) i que en el cas de comparar setmanes el període A és aquesta setmana (`ComparisonDirection`, només arguments). A més de comprovar que les respostes bones passen, hi ha tests que donen respostes dolentes a propòsit per demostrar que cada avaluador falla quan toca. S'executen amb `uv run pytest agent`.
- **Live (mai a CI):** mesura models reals d'Ollama amb els mateixos casos i els mateixos avaluadors. Mesura el temps fins al primer token, la latència total i els tokens/s, i imprimeix les respostes que fallen.

```bash
cd spine_monitor/agent
SPINE_AGENT_EVALS_LIVE=1 uv run --no-sync python -m evals.run_live \
    --models qwen3.5:4b gemma4:e4b qwen3.5:9b --repeat 1 --dump failures.jsonl
```

## Nota del model

Mesurat el 2026-11-06 amb Ollama 0.40.14 **a Docker, només CPU** (Intel Core Ultra 7 155H, 32 GB de RAM, VM de Docker amb 16 GB; sense GPU dedicada), **una repetició** per model.

| Model | Mida (Q4_K_M) | Encert | Primer token (mediana / p90) | Latència total (mediana) | Tokens/s | Casos |
|---|---|---|---|---|---|---|
| **`qwen3.5:4b`** (triat) | 3,3 GB | **81 %** (26/32) | 27,2 s / 60,7 s | 30,4 s | 2,1 | 32, prompt final |
| `gemma4:e4b` | 6,6 GB | 50 % (14/28) | 0,8 s / 51,5 s | 3,6 s | 6,8 | 28, primer prompt |
| `qwen3.5:9b` | 6,6 GB | 68 % (19/28) | 40,0 s / 118,2 s | 51,6 s | 1,3 | 28, primer prompt |

Els tokens/s són tokens de sortida entre el temps dins de l'stream del model, així que inclouen el processament del prompt; per això són baixos a CPU. **Les tres files no són estrictament comparables**: `gemma4:e4b` i `qwen3.5:9b` es van mesurar primer, amb el primer prompt i 28 casos, i no es van repetir després de canviar el prompt, la gestió de dates i el dataset de demo (només es va tornar a mesurar el model triat).

**Per què `qwen3.5:4b`:** té el millor encert i gestiona bé els rebutjos (trampes de diagnòstic 6/6, peticions d'escriptura 4/4). `gemma4:e4b` és el més ràpid, però els seus 0,8 s enganyen: en 10 de les seves 14 execucions fallides no va cridar cap eina i va respondre sense llegir dades, que és just el que aquest agent no ha de fer mai. `qwen3.5:9b` és més lent i va sortir pitjor (simulació 0/4).

**El resultat del 4B canvia molt entre execucions.** El mateix model va treure 82 % (primer prompt, 28 casos), 91 % (prompt «tool first», data actual, 32 casos) i 81 % (rangs ja calculats i «no calculis nombres», 32 casos); amb una repetició compta amb uns ±10 punts. A l'última execució `simulation` (3/4; ara funciona amb `overrides` buit), `amber_explanation` (5/5), `diagnosis_trap` (6/6), `write_request` (4/4) i `no_data` (5/5) van estar sòlides. `history_trend` va ser 0/4: dos d'aquests quatre eren un avaluador que exigia `get_history` quan el model havia respost bé amb `compare_periods` (corregit després, així que la taula de dalt els continua comptant com a fallades), i els altres dos eren reals: el model va calcular diferències pel seu compte i, a «compara aquesta setmana amb l'anterior», va intercanviar quina setmana era quina.

### Límits

- **Latència a CPU.** El model triat necessita una mediana de 27 s (p90 61 s) per començar a respondre en aquest portàtil; les preguntes d'històric són les més lentes perquè els seus resultats d'eina són grans i s'han de processar a CPU. L'objectiu de 15 s que ens vam posar no el va complir cap model que a més llegís les dades. Una GPU NVIDIA dedicada o un Mac amb Apple Silicon haurien de ser molt més ràpids, però no es va mesurar. Ollama a Docker sobre Windows no fa servir la iGPU d'Intel.
- **Mostra petita.** Una repetició de 32 casos; els resultats canvien entre execucions en uns ±10 punts. Pren les xifres com un ordre de magnitud.
- **Un model de 4B de vegades falla.** Pot saltar-se una eina que havia de cridar, presentar una simulació sense dir que és hipotètica, calcular nombres que cap eina va retornar o intercanviar els dos períodes d'una comparació. El fallback només cobreix un model no disponible, no un que respon malament, i la regla «tool first» no la imposa el codi.
- **Comprovacions per paraules clau.** El postfiltre de diagnòstics i diversos avaluadors fan servir llistes de paraules en anglès, castellà i català: no detecten formulacions que no siguin a les llistes, i una afirmació només es pot corregir després d'haver-se mostrat.
- **El català és més fluix** que l'anglès i el castellà en els models petits (una execució va entendre «Mart» com una hora del dia i una altra va barrejar «ha millorat» amb un empitjorament).
- **Dades de demo, no el motor real.** El triatge del motor continua sent un stub que sempre retorna verd, així que «per què estic en ambre?» només funciona amb les dades de demo del servidor MCP, i allà `simulate_scenario` és una aproximació lineal simple.
