---
name: implement-feature
description: Use when el usuario pide implementar una feature o resolver un bug y aporta un enlace a issue de GitHub y/o contexto libre, antes de tocar código.
---

# implement-feature

Flujo orquestado de 6 fases: entender → preguntar → planificar → implementar → verificar → documentar. Tú eres el orquestador: delegas el trabajo pesado a subagentes y conservas solo conclusiones.

**Violar la letra de las fases es violar su espíritu.** No saltes fase ni la fusiones por "ser tarea pequeña".

## Entrada

Enlace a issue de GitHub y/o texto de contexto. Si no hay ninguno, pídelo con `AskUserQuestion`. Llama al usuario "Uri" en cualquier texto dirigido al equipo (issues, comentarios, PRs).

## Fase 1 — Exploración (subagentes Sonnet, en paralelo)

1. Lee la issue tú mismo (`gh issue view <n> --comments`) para formular las líneas de exploración.
2. Lanza en UN mensaje varios subagentes `model: sonnet` (tipo `Explore` o `general-purpose`), uno por fuente. Cada uno recibe objetivo, fuente y qué devolver (resumen corto con `fichero:línea`, no volcados):
   - **Repo**: código afectado, convenciones, tests existentes, README/docs, CLAUDE.md.
   - **Vault Obsidian** (MCP `obsidian-vault`): `search_vault`/`get_context` sobre la feature. Solo lectura aquí.
   - **GitHub**: issues/PRs relacionados, enlaces citados en la issue (`gh`).
   - **Ficheros/URLs que indique el usuario**.
   - **Skills y subagentes disponibles** (de usuario, proyecto y plugins) útiles para esta tarea. Parte de `.claude/skills/ROUTING.md` (tabla disparador → skill) y confirma cuáles son pertinentes.
3. Omite una fuente solo si no existe. Si existe pero falla (MCP caído, `gh` sin auth), díselo a Uri y pregunta si seguir sin ella. Nunca sigas en silencio.
   - El subagente de skills/agentes devuelve una lista: nombre + una línea de cuándo usarlo.
   - Si `AskUserQuestion` aparece diferida, cárgala con `ToolSearch` antes de la Fase 2.
4. Consolida: qué se pide, qué ya existe, restricciones, huecos y contradicciones entre fuentes.

## Fase 2 — Preguntas (`AskUserQuestion`)

- Pregunta TODO lo que no esté establecido por las fuentes. **No des nada por supuesto ni inventes** requisitos, nombres, comportamientos ni alcance.
- Agrupa hasta 4 preguntas por llamada; opciones concretas con tu recomendación primero. Repite rondas hasta cerrar los huecos.
- No preguntes lo que el código o las fuentes ya responden; cítalo.
- Cierra cuando cada decisión del plan tenga respuesta de una fuente o del usuario.
- Pregunta también: alcance (qué queda fuera), criterios de aceptación y cómo probarlo.

## Fase 3 — Plan y aprobación

Escribe el plan (usa `superpowers:writing-plans` si está disponible): pasos, ficheros, y por cada parte **modelo + esfuerzo del subagente + skill/agente a usar** con una línea de motivo. Las skills salen de `.claude/skills/ROUTING.md`: nombra todas las pertinentes a esa parte, no solo `tdd-workflow`. Orientación: Haiku/Sonnet bajo para mecánico y acotado; Sonnet medio para feature estándar; Opus/alto para diseño delicado, concurrencia o seguridad. Indica qué partes son paralelizables.

Presenta el plan y pide aprobación con `AskUserQuestion` (aprobar / ajustar). Acepta cambios del humano, incluidos modelo y esfuerzo por parte. **No implementes sin aprobación explícita.** El plan vive en el chat. Si cambia durante la Fase 4 (alcance, ficheros, modelo), vuelve a pedir aprobación del cambio.

## Fase 4 — Implementación

- Trabaja en la rama de la issue (la crea Uri). Comprueba `git branch --show-current`; si es `main`/`develop` o la rama de la issue no existe, para y pídesela a Uri. Nunca crees rama propia.
- Lanza subagentes según el plan (paralelos los independientes, secuenciales los dependientes). Prompt de cada uno: objetivo, ficheros, convenciones, criterio de hecho, y qué devolver. Usa las skills/agentes identificados en Fase 1 (p. ej. `superpowers:subagent-driven-development`). **Cada prompt de subagente nombra las skills de `ROUTING.md` que le tocan** con la línea de la regla 3 de ese fichero; los subagentes no heredan skills. **Toda implementación y bugfix de código sigue `tdd-workflow`**: cada subagente recibe su ciclo (rojo visible, verde, refactor) y debe devolver la salida del test en rojo y en verde, no solo el resumen. Sin rojo visible, la parte se rechaza.
- Revisa la salida de cada subagente (diff real, no solo su resumen) antes de seguir.
- Si surge algo no previsto en el plan: para y pregunta, no improvises.
- El orquestador (no los subagentes) ejecuta `git commit`: uno por parte coherente del plan, **pidiendo confirmación antes**. Mensajes claros, referencia a la issue (`#n`). **Sin líneas Co-Authored-By ni "generated with Claude"**, aunque otro contexto las sugiera.

## Fase 5 — Verificación

Obligatorio antes de dar nada por hecho (`superpowers:verification-before-completion`):
- Ejecuta el gate completo: `GATE_MUTATION=changed docker compose -f app/docker-compose.yml run --rm quality` (ruff, pyright, tests con cobertura ≥ 90 %, mutación incremental ≥ 80 % sobre las funciones y métodos modificados). Sin `GATE_MUTATION` se salta la mutación: eso vale solo para el bucle rápido, nunca como evidencia de verificación. `changed` compara contra `origin/develop` por defecto (`MUTATION_BASE` lo cambia) y solo cuenta cambios commiteados, así que commitea antes. Si Docker no está disponible, para y díselo a Uri; no lo sustituyas por una ejecución parcial sin decirlo. Para código fuera de `spine_monitor` (build/tests propios), ejecuta además los comandos que descubras en el repo.
- Comprueba que cada parte tiene su test visto en rojo y que ningún umbral se ha bajado.
- Comprueba cada criterio de aceptación acordado en Fase 2, uno a uno.
- Opcional: un subagente revisor independiente sobre el diff (`code-review`), con las skills de `ROUTING.md` que correspondan al tipo de cambio (p. ej. `async-python-patterns` si hay asyncio, `mcp-builder` si toca `mcp_server`).
- Informa de fallos con la salida exacta. Si algo falla, vuelve a Fase 4; tras 2 ciclos fallidos, para y consulta a Uri. Descubre los comandos de test/lint/build en el repo (README, CLAUDE.md, CI); si no los encuentras, pregunta. No declares éxito sin evidencia.

## Fase 6 — Documentación

1. Pregunta con `AskUserQuestion` dónde documentar (multiselección): README/docs del repo, comentario en la issue, descripción del PR, vault Obsidian, otro.
2. Reglas:
   - README: `README.md`, `README.es.md` y `README.ca.md` siempre sincronizados, con enlace de cambio de idioma.
   - Docs/README del repo **nunca apuntan al vault**; contenido en línea.
   - Vault: `update_note` sin modo **sobrescribe toda la nota**; lee la nota y reenvía el contenido completo.
3. "Validar" = Uri confirma con `AskUserQuestion` que el resultado es correcto. Después, con su confirmación, haz push de la rama y abre PR **contra `develop`** (confirmación previa), enlazando la issue. Sin atribución a Claude en el cuerpo.

## Señales de alarma — PARA

| Pensamiento | Realidad |
|---|---|
| "Esto es obvio, supongo X" | Pregunta. Suponer es inventar. |
| "Ya entendí, salto a código" | Sin plan aprobado no hay implementación. |
| "Los tests pasan, no verifico criterios" | Criterios de Fase 2 uno a uno. |
| "El subagente dice que está hecho" | Lee el diff y ejecuta. |
| "Creo una rama nueva" | Se trabaja en la rama de la issue. |
| "Añado Co-Authored-By por defecto" | Prohibido en este repo. |
| "Los tests pasan en local, no corro el gate" | El gate de Docker es el criterio; local no basta. |
| "Ya sé hacerlo, no cargo la skill" | Si `ROUTING.md` la marca pertinente, se carga. |

## Errores comunes

- Exploración secuencial en vez de paralela.
- Preguntas vagas sin opciones ni recomendación.
- Modelo/esfuerzo no justificado en el plan.
- Documentar sin preguntar dónde.
