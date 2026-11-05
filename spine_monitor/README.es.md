[English](README.md) · **Español** · [Català](README.ca.md)

# spine_monitor

Workspace de uv con los paquetes Python: `contracts`, `physiology`, `engine`, `mcp_server`, `agent` y `backend`.

## Calidad y TDD

El código de producción se escribe test primero: un test en rojo visible, luego verde, luego refactor; un bugfix empieza con un test de regresión. El ciclo está descrito en la skill del proyecto `tdd-workflow` (`.claude/skills/tdd-workflow/`).

Bucle rápido durante el desarrollo:

```bash
cd spine_monitor && uv run pytest <ruta> -q -p no:randomly
```

Puerta de calidad (desde la raíz del repo). Ejecuta ruff, pyright y los tests con cobertura (líneas y ramas >= 90 %, orden aleatorio, timeout, contra un mosquitto real):

```bash
docker compose -f app/docker-compose.yml run --rm quality
GATE_MUTATION=changed docker compose -f app/docker-compose.yml run --rm quality
GATE_MUTATION=full docker compose -f app/docker-compose.yml run --rm quality
```

Equivalente en PowerShell de la forma con variable de entorno:

```powershell
$env:GATE_MUTATION='changed'; docker compose -f app/docker-compose.yml run --rm quality
```

- La cobertura `fail_under` es una única cifra combinada de líneas y ramas (la cobertura de ramas está activada): el umbral del 90 % se aplica a ese total combinado, no a cada una por separado.
- Tras cambiar `pyproject.toml` o `uv.lock` hay que reconstruir la imagen de calidad: `docker compose -f app/docker-compose.yml build quality`, o añadir `--build` a `run`.
- El mutation testing no se ejecuta por defecto. Con `GATE_MUTATION=changed` es incremental: solo las funciones y métodos cuyas líneas cambian en tu trabajo commiteado respecto a `origin/develop` (o respecto a `MUTATION_BASE` si está definida), y su puntuación de mutación agregada debe ser >= 80 %. Las líneas cambiadas fuera de cualquier función (imports, constantes, atributos de clase) no añaden mutantes, porque mutmut nunca las muta. Muestra mínima (decisión consciente de Uri): cuando `changed` puntúa menos de 10 mutantes (`MUTATION_MIN_SAMPLE`, 0 desactiva la regla), una puntuación por debajo del 80 % se imprime con el aviso `score shown, not enforced` y el código de salida es 0, porque un mutante que cambia mueve la puntuación 100/N puntos y el ruido medido es de unos 3 mutantes por ejecución; por tanto, un cambio que selecciona menos de 10 mutantes puede pasar sin que sus tests los maten. Los errores de infraestructura siguen haciendo fallar la puerta.
- Con `GATE_MUTATION=full` cubre todos los paquetes, solo informa y nunca falla; una ejecución completa de physiology tarda unos 108 minutos medidos en una máquina de 22 CPU, y se estima un orden de magnitud más lenta en un runner alojado de 2 vCPU, por encima del máximo de 360 minutos del job, así que se ejecuta en local con `GATE_MUTATION=full docker compose -f app/docker-compose.yml run --rm quality`. El workflow `full-mutation` es solo manual (`workflow_dispatch`), para un runner propio o más grande o para un único paquete (entrada `packages`, reenviada como `MUTATION_PACKAGES`).
- Las ejecuciones de mutación (`changed` y `full`) no mutan las llamadas de logging: los argumentos de sentencias como `_log.warning("...", x)` (texto del mensaje, argumentos de formato, `exc_info`) no cambian ningún comportamiento que los tests observen, así que `quality/mutation.sh` genera el `do_not_mutate_patterns` de mutmut para saltar exactamente las sentencias que empiezan una llamada `debug`/`info`/`warning`/`warn`/`error`/`exception`/`critical`/`log` sobre un objeto `log`, `_log`, `logger` o `_logger`. No se salta nada más y el código de producción no lleva `# pragma: no mutate`. Hoy esto quita 125 de los 8.993 mutantes de physiology, todos en `gateway/app.py`.
- Marcadores: cada test lleva exactamente uno de `unit`, `integration`, `contract`, `property` o `golden` (`mosquitto` es adicional). `conftest.py` asigna `unit` por defecto, `integration` a los tests marcados con `mosquitto` y `contract` a los tests bajo `contracts/tests`.
- Para regenerar a propósito el snapshot golden `physiology/tests/golden/tiny-mission.json`, ejecuta el test golden con `UPDATE_GOLDEN=1` y revisa el diff.
- CI: cada PR a `develop` ejecuta la puerta completa con mutación incremental y exige cobertura del diff >= 90 % en las líneas cambiadas; el informe completo de mutación se ejecuta a mano (ver arriba), no de forma programada. El job de PR tiene un timeout de 240 minutos.
