---
name: contract-first
description: Use when se va a cambiar o añadir un JSON Schema, un endpoint de OpenAPI, un topic de AsyncAPI, un ejemplo de contrato o algo que dependa de los modelos o tipos generados desde ellos.
---

# contract-first

Los contratos de `spine_monitor/contracts/` son la fuente de verdad. Primero el contrato, después todo lo demás. Todo cambio de código sigue `tdd-workflow` (rojo visible antes de tocar código o regenerar); aquí solo se añade el orden propio de los contratos.

## Qué es fuente y qué es derivado

| Fuente (se edita a mano) | Derivado (NO se edita a mano) |
|---|---|
| `contracts/schemas/v1/**/*.schema.json` | `contracts/src/spine_contracts/models/` (Pydantic) |
| `contracts/openapi.yaml` (engine), `contracts/app-openapi.yaml` (API de lectura de la app) | `app/ui/src/contracts/v1.ts` (tipos TS) |
| `contracts/asyncapi.yaml` (topics MQTT) | |
| `contracts/catalog/feature-catalog.json` (+ su `.schema.json`) | |
| `contracts/examples/valid/**` | |

`contracts/src/spine_contracts/topics.py` es a mano pero espeja los patrones de `common.schema.json` y las direcciones de `asyncapi.yaml`; un test los mantiene sincronizados.

## Orden de pasos (cwd: `spine_monitor/` salvo que se indique)

1. **Rojo primero.** Escribe/ajusta el test de contrato (marker `contract`, ver abajo) y ejecútalo: debe fallar por la razón correcta.
2. **Edita el schema** (o OpenAPI/AsyncAPI). Cada schema lleva `$id` que coincide con su ruta; los `$ref` apuntan a schemas existentes.
3. **Ejemplos.** Ajusta o crea el ejemplo válido en `contracts/examples/valid/<carpeta>/<nombre>.json`. Los inválidos no son ficheros: se añade una tupla a `MUTATIONS` en `contracts/tests/test_schemas.py` (`(schema, motivo, lambda d: ...)`) que rompe un ejemplo válido y debe ser rechazada.
4. **Regenera los modelos Python:** `uv run python contracts/scripts/generate_models.py` (borra y recrea `models/`; necesita `datamodel-code-generator`, black e isort del workspace).
5. **Regenera los tipos TS:** `cd app/ui && pnpm run gen:types` (lee los schemas locales, no descarga nada).
6. **Tests de contrato:** `uv run pytest contracts/tests -q -p no:randomly`. Cubren schemas válidos y refs, ejemplos, mutaciones rechazadas, modelos al día (`test_generated_models_are_up_to_date`), topics y referencias de AsyncAPI/OpenAPI.
7. **Schemathesis** si cambia una API HTTP: `uv run pytest engine/tests/test_contract_openapi_fuzz.py backend/tests/test_read_api_contract_fuzz.py -q -p no:randomly` (fuzz contra `openapi.yaml` y `app-openapi.yaml`).
8. **Drift OpenAPI:** `backend/tests/test_read_api.py` compara `app-openapi.yaml` con `client.app.openapi()` usando `spine_contracts.openapi_drift.diff` (rutas, métodos y `operationId`; los cuerpos se validan contra los schemas). Corre con el resto: `uv run pytest backend/tests/test_read_api.py -q`.
9. **Drift TS:** `cd app/ui && pnpm run check:types` (falla si `v1.ts` está desfasado y además hace `tsc --noEmit -p tsconfig.contracts.json`).
10. **Gate** (igual que en CI, lo exige `tdd-workflow`): `GATE_MUTATION=changed docker compose -f app/docker-compose.yml run --rm quality`. Commitea antes de usar `changed`.

## Cosas nuevas

- **Schema nuevo:** `contracts/schemas/v1/<telemetry|domain|engine|app|mcp>/<nombre>.schema.json` con `$id` coherente con la ruta + ejemplo válido (hay un test que exige un ejemplo por schema) + mutaciones inválidas; regenera modelos y tipos; exporta el modelo en `spine_contracts/__init__.py` si los consumidores lo importan (a mano, comprueba que `__init__` no es generado).
- **Endpoint nuevo:** primero en `openapi.yaml` o `app-openapi.yaml` (`operationId` único, parámetros de path declarados; la API de la app es solo `GET`, errores RFC 9457 `app/error`), referenciando schemas existentes. Después la implementación FastAPI, y el test de drift y Schemathesis deben quedar en verde.
- **Topic nuevo:** primero `asyncapi.yaml` (dirección, QoS, retain, schema del payload), luego el helper en `topics.py` con validación de ids; `test_topics.py` y `test_interface_docs.py` deben pasar.
- **Feature/métrica nueva:** entrada en `catalog/feature-catalog.json`; los rangos los define Aguado, no los inventes.

## Versionado

- `schema_version` es semver con major 1 en todos los mensajes; los schemas viven en `v1/`.
- Añadir un campo opcional o un valor permitido que los consumidores toleran: compatible, sube minor.
- Quitar/renombrar un campo, hacer obligatorio uno opcional, estrechar un rango o cambiar una unidad: rompe, sube major (nuevo directorio `v2/`). Se decide con Uri y se avisa a los dueños de las otras capas (gateway, engine, app, MCP).
- Los modelos Pydantic no aplican las reglas `if/then`; en las fronteras usa `validation.validate`.

## Docs

Si cambias `contracts/README.md`, actualiza `README.es.md` y `README.ca.md` en el mismo commit, con el enlace de cambio de idioma. Sin referencias externas al repo.

## Señales de alarma — PARA

| Pensamiento | Realidad |
|---|---|
| «Edito el modelo generado, es un arreglo de una línea» | `models/` se borra y recrea. El arreglo va en el schema o en los flags de `generate_models.py`. |
| «Edito `v1.ts` a mano, es solo un tipo» | Se regenera con `pnpm run gen:types`; `check:types` lo detecta. |
| «Cambio el código y ya actualizo el contrato después» | Contract-first: schema/OpenAPI/AsyncAPI y test primero. |
| «Basta con que valide Pydantic» | No aplica `if/then`; comprueba también con `validation.validate` y los tests de contrato. |
| «Es solo un campo nuevo, no hace falta ejemplo» | Un test exige un ejemplo válido por schema y las mutaciones cubren cada defecto. |
| «Renombro el `operationId` en FastAPI y ya» | El drift test falla; cambia primero el contrato. |
| «Los tests de contrato pasan, no regenero TS» | La UI usa `v1.ts`; corre `pnpm run check:types`. |
| «Es compatible aunque quite un campo» | Quitar o estrechar rompe: major y aviso a Uri y a los dueños. |
