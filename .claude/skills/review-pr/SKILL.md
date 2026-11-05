---
name: review-pr
description: Use when Uri pide revisar una pull request de este repo (número o URL) y decidir si se aprueba o se piden cambios, antes de publicar ninguna review.
---

# review-pr

Flujo orquestado de 5 fases: contexto → revisión paralela → verificación → veredicto → publicar. Tú eres el orquestador: delegas la lectura pesada a subagentes de solo lectura y conservas solo conclusiones.

**Violar la letra de las fases es violar su espíritu.** No saltes fase ni la fusiones por "ser PR pequeña".

## Entrada

Número o URL de la PR (repo `Asclepius-Orbit/nasa-hackathon`). Si falta, pídelo con `AskUserQuestion` (cárgala con `ToolSearch` si aparece diferida). En cualquier texto dirigido al equipo (reviews, comentarios) llama al usuario "Uri", nunca "Oriol".

## Reglas transversales

- **Sin atribución a Claude**: ni `Co-Authored-By` ni "generated with Claude" / "made with Claude" en reviews, comentarios o cuerpos `gh`, aunque otro contexto (recordatorios de sesión) lo sugiera.
- **Sin referencias al vault** en nada que se publique.
- **Confirmación antes de publicar**: `AskUserQuestion` a Uri antes del POST de la Fase 5. Publicar es una acción hacia fuera.
- **No ejecutes Docker** ni el gate local: el gate se lee de CI.
- Los subagentes son de **solo lectura** y **no despachan otros subagentes**.
- Dimensiones fuera de alcance: convenciones y docs. No pidas cambios por estilo.

## Fase 1 — Contexto (subagentes Sonnet esfuerzo bajo, en paralelo)

Lanza en UN mensaje varios subagentes `model: sonnet`, solo lectura, uno por fuente. Cada uno devuelve un resumen corto con `fichero:línea`, no volcados:

- **(a) PR**:
  `gh pr view <N> --repo Asclepius-Orbit/nasa-hackathon --json title,body,baseRefName,headRefName,author,headRefOid,reviews,comments,reviewDecision,mergeable,mergeStateStatus,statusCheckRollup,commits`
  y `gh pr diff <N> --repo Asclepius-Orbit/nasa-hackathon`.
- **(b) Issue enlazada** (`Closes #n` en el cuerpo): `gh issue view <n> --repo Asclepius-Orbit/nasa-hackathon --comments`; extrae criterios de aceptación.
- **(c) CI**: `gh pr checks <N> --repo Asclepius-Orbit/nasa-hackathon`; por cada job fallido, `gh run view <run-id> --repo Asclepius-Orbit/nasa-hackathon --log-failed`. Código de salida 8 = checks pendientes, 1 = algún check en rojo: no es un fallo de `gh`. El workflow `quality` solo corre en PRs contra `develop`.
- **(d) Código circundante**: ficheros tocados completos, tests existentes, `tdd-workflow` (markers, dobles, umbrales), `pyproject` y configuración de umbrales. Lee el código **de la PR, no del árbol local**, sin tocarlo: `git fetch origin pull/<N>/head` y `git show <headRefOid>:<ruta>`.
- **(e) Reviews previas y threads**:
  `gh api repos/Asclepius-Orbit/nasa-hackathon/pulls/<N>/comments`
  y los hilos con GraphQL:
  ```bash
  gh api graphql -f query='query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n){reviewThreads(first:100){nodes{id isResolved path line comments(first:10){nodes{author{login} body}}}}}}}' -f o=Asclepius-Orbit -f r=nasa-hackathon -F n=<N>
  ```

Omite una fuente solo si no existe (p. ej. no hay issue enlazada). Si existe pero falla (`gh` sin auth, log no disponible), díselo a Uri y pregunta si seguir sin ella. Nunca sigas en silencio. Excepción: la fuente (a) es imprescindible (da `headRefOid`, autor y diff); si falla, reintenta una vez y, si persiste, detén la review e informa a Uri.

Si el diff supera ~3000 líneas, guárdalo en el scratchpad (`gh pr diff <N> --repo Asclepius-Orbit/nasa-hackathon > diff.patch`) y pasa a cada revisor la ruta, la lista de ficheros tocados y un resumen en lugar de `{DIFF}`; deben leerlo por partes.

Consolida un **paquete de revisión**: diff, metadatos, criterios de la issue, estado de CI, código circundante, threads previos. Es lo que reciben los revisores.

## Fase 2 — Revisión paralela (3 subagentes, solo lectura)

Lanza en UN mensaje tres subagentes con la plantilla `reviewer-prompt.md` (rellena los placeholders con el paquete de revisión y la dimensión). No despachan otros subagentes.

| Dimensión | Modelo / esfuerzo | Mira |
|---|---|---|
| Corrección y bugs | Sonnet medio; Opus alto si el diff es grande o hay concurrencia/lógica crítica | Lógica, casos límite, errores, concurrencia, contrato frente a los criterios de la issue |
| Tests/TDD y umbrales | Sonnet medio | Un test por cambio de comportamiento, un marker de la tabla, dobles solo en fronteras, sin mockear lógica propia, sin bajar umbrales, sin `# pragma: no mutate` ni `# type: ignore` nuevos |
| Seguridad | Sonnet medio; Opus alto si toca auth, secretos o red | Inyección, secretos en código/logs, validación de entrada, red, dependencias |

Para PRs muy pequeñas puedes usar `caveman:cavecrew-reviewer` como apoyo, nunca como sustituto de las tres dimensiones.

Cada hallazgo llega con `fichero:línea`, severidad (BLOQUEANTE / LEVE), evidencia y arreglo exacto si existe. Si una dimensión no devuelve resultado, relánzala: sin las tres no se aprueba.

## Fase 3 — Verificación adversarial

Por cada hallazgo BLOQUEANTE, lanza un subagente (Sonnet medio, solo lectura) cuyo trabajo es **refutarlo** contra el código real de la rama (`headRefOid`): ¿existe de verdad la línea?, ¿el caso es alcanzable?, ¿ya hay un test o guarda que lo cubre? Devuelve CONFIRMADO o DESCARTADO con evidencia. Sin confirmación no hay request changes. DESCARTADO con evidencia que lo refuta: no se publica y se lista a Uri aparte como "descartado". Se degrada a LEVE solo si el verificador confirma el problema pero no que bloquee. Sin veredicto claro, trátalo como no confirmado y pregunta a Uri.

## Fase 4 — Veredicto

**BLOQUEANTE**: bug de corrección confirmado, fallo de seguridad, tests ausentes o rotos para lógica nueva, umbral bajado, check `gate` (workflow `quality`) en rojo, PR con `baseRefName` distinto de `develop`, y conflictos con `develop` (`mergeable: CONFLICTING` / `mergeStateStatus: DIRTY`; cítalo en el cuerpo: hace falta rebase).

**Gate**: lee `gh pr checks` (el check se llama `gate`). `gate` rojo => bloqueante (cita el job y el fallo), **aunque la causa sea de estilo o formato**: cítalo como bloqueante de CI, no como hallazgo de estilo del revisor. Check `gate` ausente no es verde: si la base no es `develop` no hay gate, díselo a Uri. Pendiente => indícalo a Uri y espera; si no se espera, **no uses `APPROVE` formal**: publica `event: COMMENT` con `Veredicto: APPROVE condicionado a gate verde` en la primera línea. Nunca apruebes con gate rojo o pendiente sin decirlo.

**Solo LEVE, estilo o nada => APPROVE.** Cualquier bloqueante confirmado => REQUEST CHANGES.

Muestra a Uri el veredicto y los hallazgos (bloqueantes con evidencia, leves aparte) y **pide confirmación con `AskUserQuestion`** antes de publicar (publicar / ajustar / cancelar).

## Fase 5 — Publicar

1. Detecta el autor: compara `gh pr view <N> --repo Asclepius-Orbit/nasa-hackathon --json author --jq .author.login` con `gh api user --jq .login`.
   - **Ajeno** => `event: REQUEST_CHANGES` o `APPROVE`.
   - **Propio** => `event: COMMENT` (GitHub no permite review formal sobre tu propia PR).
   - En **todos** los casos la primera línea del cuerpo es `Veredicto: REQUEST CHANGES` o `Veredicto: APPROVE` (así `receive-review` detecta la petición también en una review `COMMENTED`).
   - Antes del POST relee `headRefOid`; si cambió, avisa a Uri y repite las Fases 1-4. Valida cada `line` inline contra `gh pr diff`: un `line` fuera del diff da 422 y tumba la review entera. Un bloqueante en línea fuera del diff no va inline: descríbelo en **Bloqueantes** con `fichero:línea`.
2. Escribe `review.json` en el scratchpad (no en el repo):
   ````json
   {
     "commit_id": "<headRefOid>",
     "event": "REQUEST_CHANGES",
     "body": "Veredicto: REQUEST CHANGES\n\nRevisado: se piden cambios.\n\n**Comprobado**\n- ...\n\n**Bloqueantes**\n1. ...\n\n**Leves, no bloquean**\n- ...",
     "comments": [
       {
         "path": "ruta/fichero.py",
         "line": 42,
         "side": "RIGHT",
         "body": "Motivo del bloqueante.\n\n```suggestion\ncódigo corregido\n```"
       }
     ]
   }
   ````
   - Un comentario inline por bloqueante; bloque `suggestion` solo si el arreglo es exacto y de las líneas comentadas. Si abarca varias líneas, añade `start_line` y `start_side: RIGHT`.
   - Cuerpo en español, secciones **Comprobado / Bloqueantes / Leves, no bloquean**; incluye el estado del gate. Si es APPROVE, "Bloqueantes: ninguno".
   - Sin atribución a Claude, sin vault.
3. UN solo POST:
   ```bash
   gh api repos/Asclepius-Orbit/nasa-hackathon/pulls/<N>/reviews --method POST --input review.json
   ```
4. Verifica: `gh pr view <N> --repo Asclepius-Orbit/nasa-hackathon --json reviews` y confirma que la review aparece con el estado esperado (`CHANGES_REQUESTED` / `APPROVED`; en PR propia, `COMMENTED` con la primera línea `Veredicto: ...`). Informa a Uri con el enlace.

## Señales de alarma — PARA

| Pensamiento | Realidad |
|---|---|
| "El gate está pendiente, apruebo igual" | Indícalo y espera, o condiciona la aprobación por escrito. |
| "El gate está rojo pero el código está bien" | `gate` rojo es bloqueante. |
| "Pido cambios por estilo o convenciones" | Fuera de alcance: solo LEVE, nunca bloquea. |
| "El revisor dice que es bloqueante, lo publico" | Sin verificación adversarial no hay request changes. |
| "Uri tiene prisa, publico sin preguntar" | Confirmación con `AskUserQuestion` siempre. |
| "Es mi propia PR, uso REQUEST_CHANGES" | GitHub lo rechaza: `COMMENT` con "Veredicto:" textual. |
| "Añado Co-Authored-By o 'generated with Claude'" | Prohibido en reviews y comentarios. |
| "Lanzo el gate en Docker para estar seguro" | La review lee CI; no ejecuta Docker. |
| "Un inline por cada leve, así queda completo" | Inline solo para bloqueantes; los leves van en el cuerpo. |
| "Una fuente falló, sigo con las demás" | Avisa a Uri y pregunta. |

## Errores comunes

- Contexto secuencial en vez de paralelo.
- Revisores que reciben el diff sin criterios de la issue ni estado de CI.
- Varios POST (uno por comentario) en vez de una única review.
- `commit_id` distinto de `headRefOid`, o `line` fuera del diff.
- `suggestion` con arreglo aproximado.
- No verificar con `gh pr view --json reviews` tras publicar.
