"""Composite extractor: runs the registered families and assembles the ``FeatureSet`` (#40).

Rules:

- Chunks of undeclared streams or of another session are ignored; there is never a 422/500 for an
    incoherent request: whatever cannot be computed goes into ``skipped`` (no usable chunks: 200 with whatever each family omits).
- Each computed feature carries the catalog unit and the common quality (``assess_quality`` over the
    streams it declared using, ``assessed_by="engine"``). Coverage 0 (no segments with UTC) or non-finite value
    finite -> ``skipped`` ``no_data``.
- A ``feature_id`` outside the catalog is a programming error of the family: ``UnknownFeatureIdError``.
- Deterministic output: ``features`` sorted by id and ``skipped`` by (id, reason); if an id is emitted twice
    the first occurrence wins, and a computed id never also appears in ``skipped``.
- ``window`` is the UTC range of the chunks with a clock; without anchors it is omitted.
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
    """Implements ``FeatureExtractor`` over a registry of families.

    ``clock_uncertain_ms``: if given, a feature whose clock has a larger uncertainty carries the flag
    ``clock_uncertain``. Without a value nothing is flagged (there is no default threshold).
    """

    def __init__(self, registry: ExtractorRegistry, *, clock_uncertain_ms: float | None = None) -> None:
        self._registry = registry
        self._clock_uncertain_ms = clock_uncertain_ms

    def extract(self, request: FeaturesRequest) -> FeatureSet:
        context = build_context(request, clock_uncertain_ms=self._clock_uncertain_ms)
        computed: dict[str, FeatureValue] = {}
        skipped: set[tuple[str, Reason]] = set()
        for family in self._registry:
            if request.requested_window is not None and family.family != "measured":
                continue
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
        result_window = request.requested_window or context.window
        extra: dict[str, Any] = {"window": result_window.model_dump()} if result_window else {}
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
        extra: dict[str, Any] = {} if item.n is None else {"n": item.n}  # n is optional but does not allow null
        if item.window is not None:
            extra["window"] = {"start": item.window[0], "end": item.window[1]}
        return FeatureValue(feature_id=item.feature_id, value=item.value, unit=unit, quality=quality, **extra)
