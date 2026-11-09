"""Paso puro de `/simulate`: aplica los overrides a la base antes de reevaluarla.

Solo cambia el último valor de cada feature (el conjunto más reciente por `computed_at` que la contiene);
el histórico queda igual. Los overrides se aplican en orden, así que varios sobre la misma feature se componen.
"""

import math
from collections.abc import Sequence

from spine_contracts import AssessRequest, catalog
from spine_contracts.models.domain.feature_set_schema import Feature
from spine_contracts.models.engine.simulate_request_schema import Mode, Override

from spine_engine.errors import InvalidOverrideError, UnknownFeatureError


def _latest_feature(base: AssessRequest, feature_id: str) -> Feature:
    """Último valor de `feature_id`: el del conjunto más reciente que lo contiene (ante empate, el posterior en la lista)."""
    found = None
    for feature_set in sorted(base.feature_sets, key=lambda s: s.computed_at):
        for feature in feature_set.features:
            if feature.feature_id == feature_id:
                found = feature
    if found is None:
        raise UnknownFeatureError(f"The feature {feature_id!r} has no value in the base to override.")
    return found


def _apply(value: float, override: Override) -> float:
    match override.mode:
        case Mode.SET:
            return override.value
        case Mode.ADD:
            return value + override.value
        case Mode.SCALE:
            return value * override.value


def apply_overrides(base: AssessRequest, overrides: Sequence[Override]) -> AssessRequest:
    """Copia profunda de `base` con los overrides aplicados; la entrada no se muta."""
    result = base.model_copy(deep=True)
    for override in overrides:
        if not catalog.is_known(override.feature_id):
            raise UnknownFeatureError(f"The feature {override.feature_id!r} is not in the catalog.")
        feature = _latest_feature(result, override.feature_id)
        new_value = _apply(feature.value, override)
        if not math.isfinite(new_value):
            raise InvalidOverrideError(f"The override on {override.feature_id!r} does not give a finite value.")
        feature.value = new_value
    return result
