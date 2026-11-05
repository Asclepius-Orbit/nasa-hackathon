# Plantilla: subagente de arreglos (receive-review, Fase 4)

Rellena los marcadores `<...>` y pásala como prompt de un subagente (`general-purpose`, modelo y esfuerzo según el plan aprobado). Un subagente por grupo de cambios independiente.

```
Aplicas un grupo de cambios pedidos en la review de la PR #<n> (repo Asclepius-Orbit/nasa-hackathon), rama <rama>. Trabajas en el árbol actual: no crees ramas, no hagas git commit, git push ni git rebase; el orquestador lo hace.

## Grupo de cambios

<Para cada comentario del grupo: autor, fichero:línea, texto literal del comentario, acción acordada con Uri, y evidencia de la verificación si el comentario era dudoso.>

## Ficheros del grupo

<Lista. Solo tocas estos; si necesitas otro, para y dilo.>

## Contexto

<Convenciones relevantes, tests existentes (rutas), umbrales, decisiones ya tomadas con Uri.>

## Método obligatorio (tdd-workflow)

Todo cambio de código de producción sigue este ciclo:
1. Rojo: escribe el test que describe el comportamiento pedido (un único marker: unit, integration, contract, property o golden). Ejecútalo y COMPRUEBA que falla por la razón correcta (assertion o import esperado, no sintaxis ni fixture). Guarda la salida.
2. Verde: cambio mínimo. Ejecuta y guarda la salida.
3. Refactor solo con la suite en verde.
4. Bugfix: primero test de regresión visto en rojo.
Bucle rápido: `cd spine_monitor && uv run pytest <ruta> -q -p no:randomly`.

Exentos (solo docs/README y config/infra sin lógica): verifícalos ejecutándolos y dilo.
Dobles de test solo en fronteras (HTTP, MQTT, reloj, aleatoriedad); fakes a mano antes que mock; nunca mockees lógica propia.
Prohibido: bajar umbrales, `# pragma: no mutate`, `# type: ignore` (solo `# pyright: ignore[regla]` con motivo, y si lo necesitas, para y pregunta). READMEs: si tocas uno, sincroniza README.md, README.es.md y README.ca.md.

## Cuándo parar y preguntar

Si un comentario resulta ambiguo, contradice el código o los tests, o el arreglo exige salir de los ficheros del grupo: NO improvises. Devuelve estado BLOQUEADO con la pregunta y la evidencia.

## Qué devolver (todo, no solo el resumen)

1. Estado: HECHO / BLOQUEADO (con la pregunta).
2. Por cada comentario: qué cambiaste y en qué `fichero:línea`.
3. Salida literal del test en ROJO (antes del cambio) y en VERDE (después). Sin rojo visible, el orquestador rechaza el grupo.
4. Lista de ficheros tocados y `git diff --stat`.
5. Dudas o riesgos que veas.
No escribas mensajes de commit con Co-Authored-By ni "generated with Claude". Si nombras a Uri, es "Uri", nunca "Oriol".
```
