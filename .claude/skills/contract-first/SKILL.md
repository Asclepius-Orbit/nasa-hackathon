---
name: contract-first
description: Use when changing or adding a JSON Schema, an OpenAPI endpoint, an AsyncAPI topic, a contract example or anything that depends on the models or types generated from them.
---

# contract-first

The contracts in `spine_monitor/contracts/` are the source of truth. Contract first, everything else after. Every code change follows `tdd-workflow` (visible red before touching code or regenerating); this only adds the order specific to contracts.

## What is source and what is derived

| Source (edited by hand) | Derived (NOT edited by hand) |
|---|---|
| `contracts/schemas/v1/**/*.schema.json` | `contracts/src/spine_contracts/models/` (Pydantic) |
| `contracts/openapi.yaml` (engine), `contracts/app-openapi.yaml` (the app's read API) | `app/ui/src/contracts/v1.ts` (TS types) |
| `contracts/asyncapi.yaml` (MQTT topics) | |
| `contracts/catalog/feature-catalog.json` (+ its `.schema.json`) | |
| `contracts/examples/valid/**` | |

`contracts/src/spine_contracts/topics.py` is hand-written but mirrors the patterns in `common.schema.json` and the directions in `asyncapi.yaml`; a test keeps them in sync.

## Order of steps (cwd: `spine_monitor/` unless stated)

1. **Red first.** Write/adjust the contract test (marker `contract`, see below) and run it: it must fail for the right reason.
2. **Edit the schema** (or OpenAPI/AsyncAPI). Every schema carries an `$id` that matches its path; `$ref`s point to existing schemas.
3. **Examples.** Adjust or create the valid example at `contracts/examples/valid/<folder>/<name>.json`. Invalid ones are not files: add a tuple to `MUTATIONS` in `contracts/tests/test_schemas.py` (`(schema, reason, lambda d: ...)`) that breaks a valid example and must be rejected.
4. **Regenerate the Python models:** `uv run python contracts/scripts/generate_models.py` (deletes and recreates `models/`; needs `datamodel-code-generator`, black and isort from the workspace).
5. **Regenerate the TS types:** `cd app/ui && pnpm run gen:types` (reads the local schemas, downloads nothing).
6. **Contract tests:** `uv run pytest contracts/tests -q -p no:randomly`. They cover valid schemas and refs, examples, rejected mutations, up-to-date models (`test_generated_models_are_up_to_date`), topics and AsyncAPI/OpenAPI references.
7. **Schemathesis** if an HTTP API changes: `uv run pytest engine/tests/test_contract_openapi_fuzz.py backend/tests/test_read_api_contract_fuzz.py -q -p no:randomly` (fuzz against `openapi.yaml` and `app-openapi.yaml`).
8. **OpenAPI drift:** `backend/tests/test_read_api.py` compares `app-openapi.yaml` with `client.app.openapi()` using `spine_contracts.openapi_drift.diff` (paths, methods and `operationId`; bodies are validated against the schemas). It runs with the rest: `uv run pytest backend/tests/test_read_api.py -q`.
9. **TS drift:** `cd app/ui && pnpm run check:types` (fails if `v1.ts` is out of date and also runs `tsc --noEmit -p tsconfig.contracts.json`).
10. **Gate** (same as in CI, required by `tdd-workflow`): `GATE_MUTATION=changed docker compose -f app/docker-compose.yml run --rm quality`. Commit before using `changed`.

## New things

- **New schema:** `contracts/schemas/v1/<telemetry|domain|engine|app|mcp>/<name>.schema.json` with an `$id` consistent with the path + valid example (a test requires one example per schema) + invalid mutations; regenerate models and types; export the model in `spine_contracts/__init__.py` if consumers import it (by hand; check that `__init__` is not generated).
- **New endpoint:** first in `openapi.yaml` or `app-openapi.yaml` (unique `operationId`, declared path parameters; the app API is `GET` only, RFC 9457 errors `app/error`), referencing existing schemas. Then the FastAPI implementation, and the drift test and Schemathesis must end up green.
- **New topic:** first `asyncapi.yaml` (direction, QoS, retain, payload schema), then the helper in `topics.py` with id validation; `test_topics.py` and `test_interface_docs.py` must pass.
- **New feature/metric:** entry in `catalog/feature-catalog.json`; the ranges are defined by the clinical reviewer, do not invent them.

## Versioning

- `schema_version` is semver with major 1 in all messages; the schemas live in `v1/`.
- Adding an optional field or an allowed value that consumers tolerate: compatible, bump minor.
- Removing/renaming a field, making an optional one required, narrowing a range or changing a unit: breaking, bump major (new `v2/` directory). Decide it with the maintainer and notify the owners of the other layers (gateway, engine, app, MCP).
- Pydantic models do not apply the `if/then` rules; at the boundaries use `validation.validate`.

## Docs

If you change `contracts/README.md`, update `README.es.md` and `README.ca.md` in the same commit, keeping the language-switch link. No references to anything outside the repo.

## Warning signs — STOP

| Thought | Reality |
|---|---|
| "I'll edit the generated model, it's a one-line fix" | `models/` is deleted and recreated. The fix goes in the schema or in the `generate_models.py` flags. |
| "I'll change the code and update the contract afterwards" | Contract-first: schema/OpenAPI/AsyncAPI and test first. |
| "I'll edit `v1.ts` by hand, it's just one type" | It is regenerated with `pnpm run gen:types`; `check:types` detects it. |
| "Pydantic validating is enough" | It does not apply `if/then`; also check with `validation.validate` and the contract tests. |
| "It's just a new field, no example needed" | A test requires a valid example per schema and the mutations cover each defect. |
| "I'll rename the `operationId` in FastAPI and that's it" | The drift test fails; change the contract first. |
| "Contract tests pass, I won't regenerate TS" | The UI uses `v1.ts`; run `pnpm run check:types`. |
| "It's compatible even though it removes a field" | Removing or narrowing is breaking: major, and notify the maintainer and the owners. |
