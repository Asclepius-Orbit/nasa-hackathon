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
- El mutation testing no se ejecuta por defecto. Con `GATE_MUTATION=changed` es incremental: solo los ficheros cambiados en tu trabajo commiteado respecto a `origin/develop` (o respecto a `MUTATION_BASE` si está definida), y su puntuación de mutación agregada debe ser >= 80 %.
- Con `GATE_MUTATION=full` cubre todos los paquetes, solo informa y nunca falla; una ejecución completa de physiology tarda unas 2 horas medidas en una máquina de 22 CPU, y será mucho más lenta en un runner de CI de 2 vCPU.
- Marcadores: cada test lleva exactamente uno de `unit`, `integration`, `contract`, `property` o `golden` (`mosquitto` es adicional). `conftest.py` asigna `unit` por defecto, `integration` a los tests marcados con `mosquitto` y `contract` a los tests bajo `contracts/tests`.
- Para regenerar a propósito el snapshot golden `physiology/tests/golden/tiny-mission.json`, ejecuta el test golden con `UPDATE_GOLDEN=1` y revisa el diff.
- CI: cada PR a `develop` ejecuta la puerta completa con mutación incremental y exige cobertura del diff >= 90 % en las líneas cambiadas; un workflow nocturno genera el informe completo de mutación (informativo, no bloqueante).
