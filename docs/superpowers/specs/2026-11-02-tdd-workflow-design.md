# Workflow TDD y puertas de calidad

Fecha: 2026-11-02. Estado: aprobado por Uri en el chat, pendiente de revisión del documento.

## Objetivo

Todo lo que se implemente en el proyecto sigue TDD y pasa un conjunto de puertas de calidad automáticas, idénticas en local y en CI. Una skill de proyecto describe el proceso; la configuración y el CI lo hacen cumplir.

## Alcance

Los seis miembros del workspace uv de `spine_monitor`: `contracts`, `physiology`, `engine`, `mcp_server`, `agent`, `backend`. Todo en una sola entrega, en la rama actual.

## Entregables

### 1. Skill `.claude/skills/tdd-workflow/SKILL.md` (español, versionada)

Contenido:

- **Ciclo obligatorio.** Red, green, refactor.
  - Red visible: se ejecuta el test y se muestra que falla por la razón correcta antes de escribir código de producción.
  - Green con el cambio mínimo; se muestra el verde.
  - Refactor solo con la suite en verde.
- **Bugfix.** Test de regresión que reproduce el fallo, visto en rojo, antes del arreglo.
- **Excepciones.** Solo: docs y README; config/infra sin lógica (Dockerfile, compose, pyproject, CI), que se verifica ejecutándola; spikes etiquetados como throwaway y no mergeables tal cual. Todo cambio de código fuera de eso sigue el ciclo.
- **Política de dobles de test.** Dobles solo en las fronteras: HTTP, MQTT, reloj y aleatoriedad. Se prefieren fakes escritos a mano o inyección de dependencias a `unittest.mock`. Nunca se mockea lógica propia. La integración MQTT usa mosquitto real.
- **Markers.** `unit`, `integration`, `contract`, `property`, `golden` y el `mosquitto` existente. Cada test lleva exactamente uno de los cinco primeros.
- **Qué ejecutar y cuándo.** Comandos exactos del servicio `quality`.
- **Señales de alarma.** Tabla de racionalizaciones frente a la realidad, al estilo de `implement-feature`.

### 2. Integración con `implement-feature`

- Fase 4: invocar `tdd-workflow` para toda implementación.
- Fase 5: ejecutar el gate completo (`docker compose run quality`) como evidencia de verificación; el criterio de éxito son las puertas de la sección 4.

### 3. Configuración en `spine_monitor/pyproject.toml`

- `testpaths` con los seis miembros.
- Markers registrados y `--strict-markers`.
- Dependencias de desarrollo: `pytest-cov`, `pytest-randomly`, `pytest-timeout`, `hypothesis`, `schemathesis`, `diff-cover`, ruff, type checker, herramienta de mutación.
- Coverage con ramas activadas y `fail_under = 90`.
- Ruff y type checker configurados.

### 4. Puertas bloqueantes

| Puerta | Umbral |
|---|---|
| Ruff | sin errores |
| Type checker | sin errores |
| Tests | todos verdes, en orden aleatorio, con timeout |
| Cobertura (líneas y ramas) | ≥ 90 % |
| Diff coverage en PRs | ≥ 90 % en líneas cambiadas |
| Mutación incremental en PRs | ≥ 80 % en ficheros modificados |
| Tests de contrato | todos verdes |

La mutación completa nocturna genera informe y no bloquea.

### 5. Docker

Servicio `quality` en compose, junto a mosquitto (con su healthcheck). Un único comando ejecuta lint, tipos, tests con cobertura, contratos y mutación. Local y CI usan el mismo comando y la misma imagen. Imagen Linux, de modo que la herramienta de mutación funciona aunque el host sea Windows.

### 6. GitHub Actions

- PR a `develop`: lint, tipos, tests con cobertura, diff-cover, contratos, mutación incremental.
- Nightly: mutación completa sobre todo el repo, con informe y sin bloquear.

### 7. Tests sobre el código existente

- **Contrato:** Schemathesis contra los OpenAPI (engine, backend, app); payloads MQTT validados contra `asyncapi.yaml` y los topics; tests de sincronía entre schemas, modelos Pydantic y specs, ampliando el test de deriva existente.
- **Property-based (Hypothesis):** engine (scoring, detectores) y physiology (generadores, curvas).
- **Golden:** salida de physiology con semilla fija comparada con un snapshot versionado.
- **Fault injection:** caída de broker, mensajes duplicados o desordenados, payloads corruptos y timeouts en gateway, backend y mcp_server.

## Verificaciones previas obligatorias

No se fija nada sin evidencia ejecutada en el repo con Python 3.14:

1. Herramienta de mutación: probar mutmut dentro de Docker y cosmic-ray; elegir la que funcione.
2. Type checker: probar pyright, mypy y ty; elegir el que funcione mejor.
3. Schemathesis contra las apps FastAPI actuales.

## Línea base y umbrales

Antes de activar los umbrales se mide la cobertura y la mutación actuales. Si el código existente no alcanza 90 % o 80 %, se informa de los números a Uri y se decide con él; no se bajan umbrales por cuenta propia. Los paquetes sin código real (`agent`, `mcp_server`) tienen el umbral desde su primer código.

## Orden de ejecución

1. Verificaciones previas y línea base.
2. Skill y configuración.
3. Docker `quality` y mosquitto.
4. GitHub Actions.
5. Tests de contrato, property-based, golden y fault injection, paquete a paquete.
6. Integración con `implement-feature`.

## Fuera de alcance

- Tests end-to-end con hardware real.
- Pact (consumer-driven), tests de arquitectura, pre-commit local y commits separados red/green: descartados explícitamente.
- Subdirectorios por tipo de test: los tests siguen planos y se distinguen por marker.
