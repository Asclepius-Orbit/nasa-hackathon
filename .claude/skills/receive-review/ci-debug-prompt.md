# Plantilla: subagente de diagnóstico de CI (receive-review, Fase 4)

Rellena los marcadores `<...>` y pásala como prompt de un subagente (`general-purpose`, Sonnet medio por defecto, Opus alto si el fallo es de concurrencia o seguridad). Un subagente por job fallido. Fallos independientes en paralelo; si tocan los mismos ficheros, en secuencia.

```
Diagnostica y arregla un job de CI fallido en la PR #<n> (repo Asclepius-Orbit/nasa-hackathon), rama <rama>, commit <headRefOid>. No crees ramas, no hagas git commit, git push ni git rebase; el orquestador lo hace.

## Fallo

- Job: <nombre del job / check>
- Run: <run-id>
- Log: obtén el log con `gh run view <run-id> --log-failed` y léelo entero (no solo la última línea).

## Método obligatorio (superpowers:systematic-debugging)

Carga y sigue la skill `superpowers:systematic-debugging`. Causa raíz ANTES de arreglar:
1. Lee el error completo: mensaje, traza, fichero:línea, comando que falló.
2. Reprodúcelo en local con el bucle rápido (`cd spine_monitor && uv run pytest <ruta> -q -p no:randomly`, o ruff/pyright según el paso fallido). NO lances el gate de Docker (`GATE_MUTATION=changed docker compose ...`): lo corre el orquestador en la Fase 5. Si el paso fallido es `Diff coverage` o solo se reproduce con Docker, devuelve BLOQUEADO indicando el comando exacto para que lo ejecute el orquestador. Si no se reproduce, dilo y no adivines.
3. Compara con lo que cambió en la rama y formula UNA hipótesis; pruébala con el cambio mínimo. Si falla, vuelve a investigar; no apiles arreglos.
4. Arreglo en la causa, no en el síntoma.

## Reglas

- Si el arreglo cambia código de producción: sigue `tdd-workflow`. Test de regresión visto en ROJO, luego el cambio, luego VERDE. Devuelve ambas salidas.
- Exentos (config/infra sin lógica, docs): verifícalos ejecutándolos.
- Prohibido: bajar umbrales (cobertura, mutación, diff coverage), `# pragma: no mutate`, `# type: ignore`, desactivar o saltar tests o checks, relajar el workflow para que pase.
- `gh run rerun --failed` solo si demuestras que es flaky (mismo commit pasa y falla, causa externa, con la evidencia). Nunca como sustituto del diagnóstico.
- Si la causa está fuera de tu alcance (infra, secretos, servicio externo) o el arreglo exige tocar ficheros que no te he asignado: para y devuelve BLOQUEADO con evidencia.
- Tope de 2 intentos fallidos de arreglo: si el segundo falla, para y devuelve lo que sabes.

## Ficheros asignados

<Lista de ficheros que puedes tocar.>

## Qué devolver

1. Estado: ARREGLADO / FLAKY DEMOSTRADO / BLOQUEADO.
2. Causa raíz, con `fichero:línea` y la línea del log que la prueba.
3. Reproducción local: comando y salida literal del fallo.
4. Si hay cambio de código: salida literal en ROJO (test de regresión) y en VERDE; fichero(s) tocados y `git diff --stat`.
5. Cómo comprobaste que el job concreto ya pasa en local.
6. Riesgos o fallos relacionados que veas.
No escribas mensajes de commit con Co-Authored-By ni "generated with Claude". Si nombras a Uri, es "Uri", nunca "Oriol".
```
