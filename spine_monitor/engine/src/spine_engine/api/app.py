"""Los cinco endpoints del contrato sobre backends inyectados.

Cada cuerpo se valida primero contra el JSON Schema (`spine_contracts.validation`, que recoge las
reglas condicionales que Pydantic no expresa) y después se convierte al modelo generado. Los
handlers leen el cuerpo a mano, y no con FastAPI, para que todos los errores salgan como problem
details del contrato en lugar del 422 propio de FastAPI.
"""

import json
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from spine_contracts import (
    SCHEMA_VERSION,
    AssessRequest,
    BaselineRequest,
    FeaturesRequest,
    SimulateRequest,
    SimulateResponse,
    dump,
    validation,
)
from spine_contracts.models.engine.error_schema import Error, Type
from spine_contracts.models.engine.simulate_response_schema import Projection

from spine_engine.api.problems import problem, to_errors
from spine_engine.errors import InsufficientDataError, InvalidOverrideError, UnknownFeatureError
from spine_engine.factory import default_baseline_builder, default_pipeline
from spine_engine.features.composite import CompositeFeatureExtractor
from spine_engine.features.registry import default_registry
from spine_engine.interfaces import BaselineBuilder, FeatureExtractor
from spine_engine.pipeline import AssessmentPipeline
from spine_engine.settings import EngineSettings
from spine_engine.simulation import apply_overrides, project_features

TITLES = {
    Type.INVALID_REQUEST: "Invalid request",
    Type.UNSUPPORTED_SCHEMA_VERSION: "Unsupported schema version",
    Type.INSUFFICIENT_DATA: "Insufficient data",
    Type.UNKNOWN_FEATURE: "Unknown feature",
    Type.INTERNAL: "Internal error",
}


class _Rejected(Exception):
    def __init__(self, response: JSONResponse) -> None:
        self.response = response


async def _parse[M: BaseModel](request: Request, schema: str, model: type[M]) -> M:
    try:
        payload: Any = json.loads(await request.body())
    except ValueError:
        raise _Rejected(problem(Type.INVALID_REQUEST, TITLES[Type.INVALID_REQUEST], 422, "The body is not valid JSON.")) from None

    version = payload.get("schema_version") if isinstance(payload, dict) else None
    if isinstance(version, str) and not version.startswith("1."):
        raise _Rejected(
            problem(
                Type.UNSUPPORTED_SCHEMA_VERSION,
                TITLES[Type.UNSUPPORTED_SCHEMA_VERSION],
                422,
                f"This engine speaks schema_version 1.x, got {version!r}.",
                [Error(pointer="/schema_version", message="major version must be 1")],
            )
        )

    found = validation.errors(schema, payload)
    if found:
        raise _Rejected(problem(Type.INVALID_REQUEST, TITLES[Type.INVALID_REQUEST], 422, errors=to_errors(found)))
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        errors = [Error(pointer="/" + "/".join(map(str, e["loc"])), message=e["msg"]) for e in exc.errors()]
        raise _Rejected(problem(Type.INVALID_REQUEST, TITLES[Type.INVALID_REQUEST], 422, errors=errors)) from None


def _json(model: BaseModel) -> JSONResponse:
    return JSONResponse(dump(model))


def create_app(
    *,
    pipeline: AssessmentPipeline,
    baseline_builder: BaselineBuilder,
    feature_extractor: FeatureExtractor,
) -> FastAPI:
    app = FastAPI(
        title="Spine engine API",
        version="1.0.0",
        description="Local HTTP API of the stateless prediction engine. Contract: contracts/openapi.yaml.",
    )

    @app.exception_handler(_Rejected)
    async def rejected(_: Request, exc: _Rejected) -> JSONResponse:
        return exc.response

    @app.exception_handler(InsufficientDataError)
    async def insufficient(_: Request, exc: InsufficientDataError) -> JSONResponse:
        return problem(Type.INSUFFICIENT_DATA, TITLES[Type.INSUFFICIENT_DATA], 422, str(exc))

    @app.exception_handler(UnknownFeatureError)
    async def unknown_feature(_: Request, exc: UnknownFeatureError) -> JSONResponse:
        return problem(Type.UNKNOWN_FEATURE, TITLES[Type.UNKNOWN_FEATURE], 422, str(exc))

    @app.exception_handler(InvalidOverrideError)
    async def invalid_override(_: Request, exc: InvalidOverrideError) -> JSONResponse:
        return problem(Type.INVALID_REQUEST, TITLES[Type.INVALID_REQUEST], 422, str(exc))

    @app.exception_handler(Exception)
    async def unexpected(_: Request, __: Exception) -> JSONResponse:
        return problem(Type.INTERNAL, TITLES[Type.INTERNAL], 500)

    @app.get("/health", operation_id="health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/features", operation_id="extractFeatures")
    async def extract_features(request: Request) -> JSONResponse:
        body = await _parse(request, "engine/features-request", FeaturesRequest)
        return _json(feature_extractor.extract(body))

    @app.post("/baseline", operation_id="buildBaseline")
    async def build_baseline(request: Request) -> JSONResponse:
        body = await _parse(request, "engine/baseline-request", BaselineRequest)
        return _json(baseline_builder.build(body))

    @app.post("/assess", operation_id="assess")
    async def assess(request: Request) -> JSONResponse:
        body = await _parse(request, "engine/assess-request", AssessRequest)
        return _json(pipeline.evaluate(body))

    @app.post("/simulate", operation_id="simulate")
    async def simulate(request: Request) -> JSONResponse:
        """Aplica overrides y, si se solicita, proyecta la tendencia antes de reevaluar."""
        body = await _parse(request, "engine/simulate-request", SimulateRequest)
        reference = pipeline.evaluate(body.base)
        if body.project_days is not None:
            projected, unprojected = project_features(body.base, body.overrides, body.project_days)
            scenario = pipeline.evaluate(projected)
            response = SimulateResponse(schema_version=SCHEMA_VERSION, reference=reference, scenario=scenario, hypothetical=True,
                                        projection=Projection(project_days=body.project_days, unprojected_features=unprojected))
        else:
            # Cada evaluación lleva un assessment_id nuevo; sin cambios conservamos la referencia.
            scenario = pipeline.evaluate(apply_overrides(body.base, body.overrides)) if body.overrides else reference.model_copy(deep=True)
            response = SimulateResponse(  # pyright: ignore[reportCallIssue]  # optional generated Field must remain unset
                schema_version=SCHEMA_VERSION, reference=reference, scenario=scenario, hypothetical=True
            )
        return _json(response)

    return app


def create_default_app(settings: EngineSettings | None = None) -> FastAPI:
    settings = settings or EngineSettings()
    return create_app(
        pipeline=default_pipeline(settings),
        baseline_builder=default_baseline_builder(),
        feature_extractor=CompositeFeatureExtractor(default_registry(settings)),
    )
