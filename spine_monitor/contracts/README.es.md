[English](README.md) · **Español** · [Català](README.ca.md)

# spine-contracts (v1, borrador para revisión)

Contratos de datos compartidos por todos los componentes: gateway del wearable, broker MQTT, app a bordo, motor y servidor MCP.
**Los JSON Schemas son la fuente de verdad.** Todo lo demás se genera a partir de ellos o se comprueba contra ellos.

## Estructura
| Ruta | Qué es |
|---|---|
| `schemas/v1/common.schema.json` | Definiciones comunes: ids, enums, calidad, colocación |
| `schemas/v1/telemetry/` | Mensajes MQTT: chunk de stream, estado del dispositivo, comando, ack de comando, sincronización de reloj. La entidad `domain/calibration` también se publica en el bus (ver Convenciones) |
| `schemas/v1/domain/` | Entidades: sesión, calibración, perfil del astronauta, autoinforme, contexto diario, conjunto de features, baseline, evaluación |
| `schemas/v1/engine/` | Cuerpos HTTP del motor: `/features`, `/baseline`, `/assess`, `/simulate`, errores |
| `schemas/v1/app/` | Cuerpos HTTP de la API de solo lectura de la app: estado, histórico, explicación, listas de evaluaciones, sesiones y autoinformes, errores; y las vistas de la UI de `ui-openapi.yaml` (`ui-*`) |
| `catalog/feature-catalog.json` | Cada métrica derivada (feature) con unidad, entradas y requisitos; los rangos son de Aguado |
| `catalog/protocol-catalog.json` | Datos fijos de cada protocolo (banda, repeticiones, descanso, ids de colocación) que citan las vistas de la UI |
| `asyncapi.yaml` | Topics MQTT, dirección, QoS y retain (desde el punto de vista del gateway) |
| `openapi.yaml` | API HTTP del motor |
| `app-openapi.yaml` | API HTTP de solo lectura de la app (solo GET), la usa el servidor MCP |
| `ui-openapi.yaml` | API de vistas de la app para la UI de escritorio (solo GET): una pantalla por endpoint, códigos y datos, sin prosa |
| `examples/valid/` | Un ejemplo válido por mensaje; son los mocks para todos |
| `src/spine_contracts/` | Paquete Python: `models/` generados, `messages` (uniones), `topics`, `validation`, `openapi_drift` |

## Qué mide el sistema
- **EMG** (1 canal diferencial, mV, 1000 Hz): actividad muscular, para comparar Tierra y microgravedad con el mismo ejercicio estandarizado.
- **IMU MPU-6050** (accel en g, gyro en deg/s, 100 Hz por defecto): movimiento de la espalda durante el ejercicio.
- **Cordón de estiramiento** (cuentas ADC, ohm, longitud derivada en mm, 20 Hz): elongación de la columna.
- **Autoinforme**: dolor (NRS 0-10), localización, rigidez, sueño, Oswestry semanal, banderas rojas.
- **Contexto**: fase de misión, entorno de gravedad, medicación, ejercicio y contramedidas.
- **Perfil**: edad, sexo biológico, antropometría, historia de columna y espacial, forma física.

Los datos pertenecen a una **sesión**: `exercise` (protocolo estandarizado, p. ej. jalón al pecho de pie, con carga declarada) o `spine_check` (medición pasiva corta). Solo son comparables los datos con el mismo `protocol_id`, y Tierra frente a espacio se lee a través de `gravity_environment`.

## Convenciones
- Campos y descripciones en inglés, `snake_case`.
- **Unidades canónicas fijadas por canal** (g, deg/s, mV, ohm, mm, cm, kg, Hz): no se convierte nada al recibir.
- **Los payloads MQTT son chunks**: un stream, muestras consecutivas, un array por canal, `seq` para detectar pérdidas. Cada stream tiene su propia frecuencia.
- **Tiempo**: `t0_device_ms` (monotónico) + `boot_id` en el dispositivo; el gateway añade `received_at` (UTC) y publica anclas `clock-sync`. Todo lo demás es RFC 3339 con desfase.
- **Calidad**: score 0..1 más `flags` enumeradas por canal y chunk, e indica quién la evaluó.
- `source` es `device`, `simulator` o `replay`: una demo que mezcle datos reales y simulados debe decirlo.
- **Las calibraciones viajan por el bus**: `asclepius/v1/crew/{astronaut_id}/device/{device_id}/calibration/{stream_id}` (QoS 1, retained, un topic por stream, payload = `domain/calibration`). La última vigente queda retenida, así que un consumidor puede resolver el `calibration_id` de los chunks de stretch. Lo cubren `topics.calibration`, `topics.parse_calibration` y `SUBSCRIBE_ALL_CALIBRATIONS`.
- Los ids son seudónimos (`AST-01`); ningún dato personal viaja por el bus MQTT.
- Todo mensaje lleva `schema_version` (semver, mayor 1).

## Uso desde Python
```python
from spine_contracts import stream_chunk_adapter, dump, topics, validation

chunk = stream_chunk_adapter.validate_json(payload)      # EmgChunk | ImuChunk | StretchChunk
topic = topics.stream("AST-01", "wb-01", chunk.stream_id)
validation.validate("telemetry/stream-chunk", dump(chunk))  # reglas completas, ver abajo
```
- Los modelos Pydantic comprueban estructura, tipos y rangos. **No** aplican las reglas `if/then` de los schemas (una sesión de ejercicio necesita `load_declared`, una longitud derivada del cordón necesita `calibration_id`, un escalado requerido necesita `urgency`). Llama a `validation.validate` donde entren datos al sistema.
- Serializa con `dump(model)`: solo emite los campos asignados, que es lo que esperan los schemas.
- Regenera los modelos tras cualquier cambio de schema: `uv run python contracts/scripts/generate_models.py` (desde `spine_monitor`). Un test falla si están desactualizados. Nunca edites `models/` a mano.
- Ejecuta las comprobaciones: `uv run pytest` (schemas, ejemplos, mutaciones que deben rechazarse, código generado, topics, referencias de AsyncAPI/OpenAPI).

## API de solo lectura de la app
`app-openapi.yaml` es lo que usa el servidor MCP; nunca toca la base de datos de la app. Solo `GET`: ningún endpoint modifica datos.
- **Endpoints por herramienta** devuelven el trabajo ya hecho para una herramienta MCP: `/crew/{astronaut_id}/status` (`get_current_status`), `/crew/{astronaut_id}/history?from&to` (`get_history`), `/assessments/{assessment_id}/explanation` (`explain_assessment`). `compare_periods` no tiene endpoint: el servidor MCP llama dos veces a `/history` y compara.
- **Endpoints de recurso** devuelven una entidad o una lista: evaluaciones, sesiones (solo metadatos, nunca chunks crudos), el conjunto de features de una sesión, autoinformes y baselines.
- Las listas exigen `from` y `to` (RFC 3339 con desfase) y aceptan `limit` (1-500, por defecto 100). Van de más antiguo a más reciente; si hay más elementos se conservan los más recientes y `truncated` es `true`.
- Lo que falta es `null`, y `status.freshness` lo marca `stale: true`: la ausencia nunca se presenta como buena noticia. Los umbrales de `stale` los decide la app.
- Los errores son problem details RFC 9457 (`app/error`), como en el motor.
- La app construye esta API con FastAPI. El contrato es la referencia: compara su documento generado con `openapi_drift.diff(contract, app.openapi())`, que lista rutas y `operationId` añadidos, quitados o renombrados.
- `from` es palabra reservada de Python: los modelos generados lo llaman `from_` y conservan `from` como alias. `dump` emite alias.

## Cambiar un contrato
Edita el schema, añade o actualiza su ejemplo, regenera, ejecuta los tests y avisa a los dueños de las demás capas. Las ampliaciones compatibles suben la versión menor; cualquier otra cosa, la mayor.

## Abierto
- **Aguado:** validar el catálogo de features (ids, unidades, `risk_direction`, rangos y sus fuentes); el conjunto reducido de ítems del ODI; el protocolo exacto del ejercicio (repeticiones, carga, procedimiento de MVC) y la colocación de electrodos e IMU; el modelo del cordón (no es lineal y tarda en recuperarse, así que la longitud sale de una curva medida).
- **Polfer:** ¿el módulo EMG da la señal cruda o una envolvente? (las features espectrales necesitan cruda). Usar pines ADC1 del ESP32 (el ADC2 no funciona con Bluetooth/WiFi). El formato wearable→gateway es libre; el contrato empieza en MQTT. El gateway real usa el protocolo serie documentado en `spine_monitor/gateway/README.es.md` y `firmware/README.es.md`.
- **Frecuencia de la IMU:** 100 Hz por defecto; 10 Hz se queda corta para suavidad y velocidad pico de una repetición de ~2 s.
- Salidas de las herramientas MCP: `compare_periods` tiene su propio schema, `mcp/compare-periods` (lo calcula el servidor con dos llamadas a `/history`); las demás herramientas MCP reutilizan los schemas `app` y `engine`.
- Los tipos TypeScript de la UI se generan de estos schemas en `app/ui/src/contracts/v1.ts` (`pnpm run gen:types` en `app/ui`; `pnpm run check:types` falla si están desactualizados). Regenéralos tras cualquier cambio de schema.
- Aún sin cubrir: informe a Houston (FHIR/DTN).
- Revisión: Andreea. Validación semántica: Aguado.

## Limitaciones conocidas
- En microgravedad el acelerómetro no ve la gravedad: las features de la IMU que dependen de la orientación no son comparables con la Tierra; el catálogo prefiere las basadas en el giroscopio.
- La librería Python `jsonschema` aplica `pattern` con `re.search`, y ahí `$` admite un salto de línea final; por eso `topics` usa `fullmatch`.
- Los modelos generados usan tipos `constr(...)` para que los elementos de lista con restricciones sigan siendo cadenas planas; los analizadores de tipos estáticos pueden marcarlos.
