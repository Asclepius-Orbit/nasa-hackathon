"""Extractor compuesto: ejecuta las familias registradas y arma el ``FeatureSet`` (#40).

Reglas:

- Chunks de streams no declarados o de otra sesión se ignoran; nunca hay 422/500 por una petición
  incoherente: lo no calculable sale en ``skipped`` (sin chunks utilizables, 200 con lo que cada familia omita).
- Cada feature calculada lleva la unidad del catálogo y la calidad común (``assess_quality`` sobre los
  streams que declaró usar, ``assessed_by="engine"``). Cobertura 0 (sin segmentos con UTC) o valor no
  finito -> ``skipped`` ``no_data``.
- Un ``feature_id`` fuera del catálogo es un error de programación de la familia: ``UnknownFeatureIdError``.
- Salida determinista: ``features`` ordenadas por id y ``skipped`` por (id, motivo); si un id se emite dos
  veces gana la primera aparición, y un id calculado nunca figura también en ``skipped``.
- ``window`` es el rango UTC de los chunks con reloj; sin anclas se omite.
"""

import math
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from spine_contracts import SCHEMA_VERSION, FeatureSet, FeaturesRequest, FeatureValue
from spine_contracts.catalog import unit_for
from spine_contracts.models.domain.feature_set_schema import Reason, SkippedItem

from spine_engine.features.context import ExtractionContext, build_context
from spine_engine.features.registry import ComputedFeature, ExtractorRegistry


def _engine_version() -> str | None:
    try:
        return version("spine-engine")
    except PackageNotFoundError:
        return None


class CompositeFeatureExtractor:
    """Implementa ``FeatureExtractor`` sobre un registro de familias.

    ``clock_uncertain_ms``: si se indica, una feature cuyo reloj tenga una incertidumbre mayor lleva el flag
    ``clock_uncertain``. Sin valor no se marca nada (no hay umbral por defecto).
    """

    def __init__(self, registry: ExtractorRegistry, *, clock_uncertain_ms: float | None = None) -> None:
        self._registry = registry
        self._clock_uncertain_ms = clock_uncertain_ms

    def extract(self, request: FeaturesRequest) -> FeatureSet:
        context = build_context(request, clock_uncertain_ms=self._clock_uncertain_ms)
        computed: dict[str, FeatureValue] = {}
        skipped: set[tuple[str, Reason]] = set()
        for family in self._registry:
            result = family.extract(context)
            for item in result.computed:
                unit = unit_for(item.feature_id)
                if item.feature_id in computed:
                    continue
                feature = self._feature(context, item, unit)
                if feature is None:
                    skipped.add((item.feature_id, Reason.NO_DATA))
                else:
                    computed[item.feature_id] = feature
            for omitted in result.skipped:
                unit_for(omitted.feature_id)
                skipped.add((omitted.feature_id, omitted.reason))

        session = request.session
        extra: dict[str, Any] = {"window": context.window} if context.window else {}
        return FeatureSet(
            schema_version=SCHEMA_VERSION,
            astronaut_id=session.astronaut_id,
            session_id=session.session_id,
            protocol_id=session.protocol_id,
            gravity_environment=session.mission_context.gravity_environment,
            source=session.source,
            computed_at=datetime.now(UTC),
            engine_version=_engine_version(),
            features=[computed[feature_id] for feature_id in sorted(computed)],
            skipped=[SkippedItem(feature_id=f, reason=r) for f, r in sorted(skipped, key=lambda p: (p[0], p[1].value)) if f not in computed],
            **extra,
        )

    @staticmethod
    def _feature(context: ExtractionContext, item: ComputedFeature, unit: str) -> FeatureValue | None:
        assessment = context.assess(item.streams)
        if assessment.coverage == 0.0 or not math.isfinite(item.value):
            return None
        quality = item.quality if item.quality is not None else assessment.to_quality()
        extra: dict[str, Any] = {} if item.n is None else {"n": item.n}  # n es opcional pero no admite null
        return FeatureValue(feature_id=item.feature_id, value=item.value, unit=unit, quality=quality, **extra)
