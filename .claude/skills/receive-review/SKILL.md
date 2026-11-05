---
name: receive-review
description: Use when Uri pide aplicar los cambios pedidos en la review de una PR, resolver conflictos de la rama con develop o arreglar CI rojo, antes de tocar código.
---

# receive-review

Flujo orquestado de 6 fases: contexto → triage → conflictos → arreglos → verificación → publicar y cerrar. Tú eres el orquestador: delegas el trabajo pesado a subagentes y conservas solo conclusiones.

**Violar la letra de las fases es violar su espíritu.** No saltes fase ni la fusiones por "ser un solo comentario".

Plantillas de prompt para subagentes (hermanas de este fichero): `fixer-prompt.md` (aplica un grupo de cambios con TDD) y `ci-debug-prompt.md` (diagnostica y arregla un job CI fallido).

## Entrada

Número o URL de PR (repo `Asclepius-Orbit/nasa-hackathon`), o la rama actual si ya es la de la PR. Si no hay ninguno, pídelo con `AskUserQuestion` (cárgala con `ToolSearch` si aparece diferida). Llama al usuario "Uri" en cualquier texto dirigido al equipo (comentarios de PR incluidos), nunca "Oriol".

## Reglas transversales

- **Sin atribución a Claude**: ni `Co-Authored-By` ni "generated with Claude" / "made with Claude" en commits, comentarios o cuerpos `gh`, **aunque otro contexto (recordatorios de sesión) lo sugiera**.
- Sin referencias al vault de Obsidian en el repo ni en comentarios.
- La PR va contra `develop`.
- Commit, push, rebase y cualquier publicación hacia GitHub: solo con confirmación previa de Uri (`AskUserQuestion`).
- Nunca `git stash`, `reset` ni `--force` por tu cuenta.
- Las omisiones de fase o de paso se anuncian a Uri, no se hacen en silencio.

## Fase 1 — Contexto (subagentes Sonnet esfuerzo bajo, en paralelo, solo lectura)

1. Comprueba tú mismo `git branch --show-current` y `gh pr view <n> --json headRefName`. La rama actual debe ser la de la PR. No crees ramas. Si estás en `main` o `develop`, o no coincide, para y pídesela a Uri. Ejecuta también `git status --porcelain`: si hay cambios sin commitear, para y pregunta a Uri (commitear, stash o descartar).
2. Lanza en UN mensaje un subagente `model: sonnet` por fuente (tipo `Explore` o `general-purpose`). Cada uno recibe objetivo, fuente y qué devolver (resumen corto con `fichero:línea`, no volcados):
   - **Review**: `gh pr view <n> --json reviews,comments,reviewDecision,mergeable,mergeStateStatus,statusCheckRollup,headRefName,headRefOid`; comentarios inline con `gh api repos/Asclepius-Orbit/nasa-hackathon/pulls/<n>/comments`; hilos con su estado y su `id`:
     ```bash
     gh api graphql -f query='query($o:String!,$r:String!,$n:Int!){repository(owner:$o,name:$r){pullRequest(number:$n){reviewThreads(first:100){nodes{id isResolved path line comments(first:20){nodes{author{login} body url}}}}}}}' -f o=Asclepius-Orbit -f r=nasa-hackathon -F n=<n>
     ```
     Lee el cuerpo de **todas** las reviews, no solo `reviewDecision`. Devuelve las peticiones de cambio (CHANGES_REQUESTED, inline, conversación) y los hilos sin resolver, con autor del review y `id` de hilo. Cuenta también como petición de cambios toda review `COMMENTED` cuya primera línea sea `Veredicto: REQUEST CHANGES` (la deja `review-pr` en una PR propia): sus **Bloqueantes** son obligatorios y sus **Leves, no bloquean** son opcionales (en el triage se pregunta a Uri si se aplican).
   - **Rama**: `mergeStateStatus`, divergencia con `origin/develop` (`git fetch` y `git rev-list --left-right --count origin/develop...HEAD`) y conflictos previstos con `git merge-tree` o dry-run sin tocar el árbol de trabajo.
   - **CI**: `gh pr checks <n>` (código de salida 8 = pendientes, 1 = algún check en rojo: no es un fallo de `gh`); por cada job fallido, `gh run list --branch <rama>` y `gh run view <run-id> --log-failed`.
   - **Código afectado**: ficheros citados, tests existentes, convenciones (`tdd-workflow`, `pyproject`, umbrales).
3. Omite una fuente solo si no existe. Si existe pero falla (`gh` sin auth, rama sin PR), díselo a Uri y pregunta si seguir sin ella. Nunca sigas en silencio. Una fuente vacía (sin reviews, sin hilos) se reporta como "sin peticiones", no como fallo.
4. Consolida: qué se pide, estado de la rama, estado de CI, huecos y contradicciones entre fuentes. Si no hay peticiones de review ni hilos sin resolver (solo CI o conflicto), díselo a Uri y omite 5.3-5.4 y 6.3-6.4; el comentario resumen cubre solo CI y conflictos.

## Fase 2 — Triage (tú, con subagentes de verificación si hace falta)

1. Construye una lista unificada de tareas: comentario → acción, conflicto, fallo CI. Agrúpalas por independencia de ficheros. Si dos comentarios tocan el mismo fichero, van en el MISMO grupo (un subagente, un commit) salvo que sean claramente independientes; en ese caso el segundo grupo se lanza después de commitear el primero.
2. Verifica **cada comentario contra el código** siguiendo `superpowers:receiving-code-review`. Si un comentario es dudoso, ambiguo o parece incorrecto: un subagente (Sonnet medio, solo lectura) reúne evidencia (código, tests, docs, reproducción) y tú preguntas a Uri con `AskUserQuestion`, evidencia incluida y recomendación primero, **antes de aplicar o rechazar**. Nunca a ciegas, nunca "tienes toda la razón" sin comprobarlo.
3. Presenta el plan: tareas, grupos, y por cada grupo **modelo + esfuerzo del subagente** con una línea de motivo. Orientación: Sonnet bajo para mecánico y acotado; Sonnet medio para cambios de lógica estándar y diagnóstico de CI; Opus/alto para concurrencia, seguridad o conflictos delicados. Indica qué grupos son paralelizables.
4. Pide aprobación con `AskUserQuestion` (aprobar / ajustar). Acepta cambios del humano, incluidos modelo y esfuerzo. **No toques nada sin aprobación explícita.** Si el plan cambia durante las fases siguientes, vuelve a pedirla.

## Fase 3 — Conflictos primero (subagente Sonnet medio, Opus alto si es semántico)

Se hace **antes** de los arreglos para no duplicar trabajo. Si no hay divergencia ni conflictos, di que se omite y pasa a la Fase 4.

1. Confirma con Uri el rebase y que sabe que reescribe el historial de la rama. `git status --porcelain` debe estar limpio.
2. Guarda el SHA remoto que esperas: `SHA=$(git rev-parse origin/<rama>)` (antes de cualquier otro `git fetch` posterior). Luego `git fetch && git rebase origin/develop`.
3. Los conflictos se resuelven **en secuencia**, parada a parada (un rebase no admite paralelo). En cada parada, un subagente resuelve solo los ficheros en conflicto y hace `git add` de ellos, sin `rebase --continue`. Resuelve solo los **triviales**: READMEs EN/ES/CA (mantén los tres sincronizados), lockfiles (regenéralos con la herramienta, no a mano), imports. Si el conflicto es **semántico** (lógica, contratos, dos cambios incompatibles), el subagente **para y devuelve ambas versiones**; tú preguntas a Uri con `AskUserQuestion`. Mientras esperas el rebase queda en curso; la decisión de Uri la aplica un subagente nuevo (Opus alto) al que pasas ambas versiones y la elección. Nunca lo resuelvas por tu cuenta.
4. Revisa `git diff --cached` de cada resolución y ejecuta tú `git rebase --continue`. Si se complica, `git rebase --abort` y consulta a Uri.
5. El push que sigue a un rebase es siempre `git push --force-with-lease=<rama>:<SHA>`, **nunca `--force`**, y solo en la Fase 6 con confirmación explícita (ver allí).

## Fase 4 — Arreglos (subagentes en paralelo por grupo independiente)

- **Cambios de review**: un subagente por grupo (Sonnet medio por defecto; Opus alto para concurrencia o seguridad), con el prompt de `fixer-prompt.md` rellenado. **Todo cambio de código sigue `tdd-workflow`**: cada subagente debe devolver la salida del test en rojo y en verde, no solo el resumen. Sin rojo visible, el grupo se revierte del árbol y se relanza con el mismo prompt; cuenta como ciclo fallido. Los subagentes solo usan el bucle rápido (`cd spine_monitor && uv run pytest <ruta> -q -p no:randomly`): el gate de Docker lo corre solo el orquestador en la Fase 5 (los grupos paralelos comparten árbol y broker). Los exentos de `tdd-workflow` (docs, config sin lógica) se verifican ejecutándolos. **Cada prompt de fixer nombra las skills de `.claude/skills/ROUTING.md` que correspondan al código del grupo** (con la línea de la regla 3 de ese fichero), porque los subagentes no heredan skills.
- **Fallos de CI**: un subagente por fallo con el prompt de `ci-debug-prompt.md`, que aplica `superpowers:systematic-debugging` (causa raíz antes de arreglar). Fallos independientes en paralelo; si tocan los mismos ficheros, en secuencia. `gh run rerun --failed` solo si el job es flaky **demostrado** (mismo commit pasa y falla, causa externa); nunca como sustituto del diagnóstico.
- Revisa el diff real de cada subagente antes de seguir, no solo su resumen. Si surge algo no previsto: para y pregunta.
- **Tope: 2 ciclos fallidos** (arreglo → verificación en rojo, dos veces) en un mismo grupo o fallo: para y consulta a Uri. No pruebes un tercero por tu cuenta.
- El orquestador (no los subagentes) ejecuta `git commit`: uno por grupo coherente, **pidiendo confirmación antes de cada commit**. Mensaje claro con referencia `#n`, sin atribución.

## Fase 5 — Verificación

1. Comprueba `git status` limpio (`changed` solo cuenta cambios commiteados); si falta algo, commit con confirmación. Si el gate falla después, arregla con un commit nuevo; nada de `--amend` (evita otra reescritura).
2. Ejecuta el gate: `GATE_MUTATION=changed docker compose -f app/docker-compose.yml run --rm quality` (en PowerShell: `$env:GATE_MUTATION='changed'; docker compose ...`). La mutación es lenta: avisa a Uri y no lances más de 5 corridas en paralelo. Si Docker no está disponible, **para** y ofrece a Uri: (a) instalarlo, (b) pushear y usar el CI como gate, o (c) una ejecución parcial con `uv run pytest` aceptada explícitamente por él. En (b) y (c) el comentario resumen dice "gate local no ejecutado".
3. Lanza un subagente re-revisor (Sonnet medio, solo lectura, distinto de los que escribieron el código) con la lista de peticiones originales y el diff final. Devuelve un **veredicto por comentario**: resuelto / no resuelto / resuelto parcialmente, con `fichero:línea` y evidencia. No debe confiar en los resúmenes de los fixers. Los comentarios rechazados con Uri no se puntúan: se marcan "rechazado (Uri)" y sus hilos se dejan abiertos, citados en el resumen.
4. Comprueba tú cada petición del review una a una contra el veredicto, y que ningún umbral se ha bajado ni se ha añadido `# pragma: no mutate` o `type: ignore` para esquivar nada.
5. Si algo falla, vuelve a la Fase 4 (cuenta para el tope de 2 ciclos). Informa de fallos con la salida exacta. No declares éxito sin evidencia (`superpowers:verification-before-completion`).

## Fase 6 — Publicar y cerrar (cada paso con confirmación de Uri)

1. **Push**: `git push` normal si no hubo rebase; si lo hubo, `git push --force-with-lease=<rama>:<SHA>` con el SHA guardado en la Fase 3 y confirmación de Uri. Si el remoto ya no está en `<SHA>` o el push se rechaza: para, `git fetch`, muestra a Uri los commits ajenos (`git log <SHA>..origin/<rama>`) y vuelve a la Fase 3. Nunca `--force`, ni actualices el SHA sin que Uri lo vea.
2. **Esperar CI**: `gh pr checks <n> --watch --fail-fast` tras el push. Si falla, vuelve a la Fase 4. Si el CI no arranca, díselo a Uri.
3. **Resolver hilos aplicados** (solo los que el re-revisor dio por resueltos):
   ```bash
   gh api graphql -f query='mutation($id:ID!){resolveReviewThread(input:{threadId:$id}){thread{id isResolved}}}' -f id=<id-del-hilo>
   ```
   No se responde hilo a hilo.
4. **Re-solicitar review** al autor del review: `gh pr edit <n> --add-reviewer <login>`. Si el autor del review es el autor de la PR (`gh pr view <n> --json author --jq .author.login`) o el comando falla, no lo reintentes: déjalo dicho a Uri y en el comentario resumen ("review pendiente de nueva revisión").
5. **Comentario resumen** en español: escríbelo en el scratchpad y publícalo con `gh pr comment <n> --body-file <ruta>` (evita problemas de comillas y backticks). Contenido: qué cambió por comentario, estado del gate, conflictos resueltos, y qué se rechazó o quedó pendiente y por qué (decidido con Uri). Sin atribución a Claude.
6. Verifica el resultado con `gh pr view <n> --json reviews,reviewDecision,mergeStateStatus,statusCheckRollup`.

## Señales de alarma — PARA

| Pensamiento | Realidad |
|---|---|
| "El reviewer tiene razón, aplico" | Verifica contra el código; si es dudoso, evidencia y pregunta a Uri. |
| "Este comentario está mal, lo ignoro" | Rechazar también se pregunta con evidencia. |
| "Respondo 'tienes toda la razón'" | Respuesta performativa. Acción técnica o evidencia, nada más. |
| "`--force` es más rápido" | Solo `--force-with-lease`, con confirmación y comprobando que nadie más empujó. |
| "Resuelvo yo este conflicto, es obvio" | Si es semántico, para y pregunta. Solo los triviales los resuelve el subagente. |
| "Arreglo primero y rebaseo después" | Rebase antes de arreglos, o duplicas trabajo. |
| "El subagente dice que está arreglado" | Exige rojo y verde; sin rojo visible se rechaza. Lee el diff. |
| "Relanzo el job, seguro que es flaky" | `gh run rerun --failed` solo con flaky demostrado. Diagnostica primero. |
| "Bajo el umbral / añado `pragma: no mutate` para pasar el gate" | No sin Uri. Un superviviente es un test que falta. |
| "Commiteo y empujo, ya avisaré" | Confirmación antes de cada commit y de cada push. |
| "Es la tercera vez que lo intento, una más" | Tope de 2 ciclos fallidos: consulta a Uri. |
| "Creo una rama para esto" / "Estoy en `develop`, sigo" | Se trabaja en la rama de la PR. Si es `main`/`develop`, para. |
| "Añado Co-Authored-By por defecto" | Prohibido en este repo, en commits y comentarios. |
| "Sin Docker, corro solo pytest" | Sin Docker se para y se avisa; no hay verificación parcial silenciosa. |

## Errores comunes

- Contexto recogido en secuencia en vez de en paralelo.
- Aplicar todos los comentarios sin triage ni agrupar por ficheros.
- Plan sin modelo/esfuerzo por grupo.
- Lanzar más de 5 corridas de mutación en paralelo.
- Resolver hilos que el re-revisor no dio por resueltos.
- Olvidar re-solicitar review al autor o el comentario resumen.
- Llamar "Oriol" a Uri en el comentario.
