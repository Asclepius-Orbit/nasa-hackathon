"""Paso puro de `/simulate`: aplica los overrides a la base antes de reevaluarla.

Solo cambia el último valor de cada feature (el conjunto más reciente por `computed_at` que la contiene);
el histórico queda igual. Los overrides se aplican en orden, así que varios sobre la misma feature se componen.
"""

import math
from collections.abc import Sequence
from datetime import datetime, timedelta

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


def project_features(base: AssessRequest, overrides: Sequence[Override], days: int) -> tuple[AssessRequest, list[str]]:
    """Proyecta desde el último valor modificado usando la pendiente del historial."""
    result = apply_overrides(base, overrides)
    history: dict[str, list[tuple[datetime, Feature]]] = {}
    for feature_set in sorted(result.feature_sets, key=lambda item: item.computed_at):
        for feature in feature_set.features:
            history.setdefault(feature.feature_id, []).append((feature_set.computed_at, feature))

    unprojected = []
    for feature_id, observations in sorted(history.items()):
        start, end = observations[0][0], observations[-1][0]
        if start == end:
            unprojected.append(feature_id)
            continue
        if timedelta(days=days) > end - start:
            raise InvalidOverrideError(f"Projection of {feature_id!r} for {days} days exceeds its history of {end - start}.")
        xs = [(at - start).total_seconds() / 86400 for at, _ in observations]
        ys = [feature.value for _, feature in observations]
        try:
            x_mean = math.fsum(xs) / len(xs)
            y_mean = math.fsum(ys) / len(ys)
            slope = math.fsum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys, strict=True)) / math.fsum(
                (x - x_mean) ** 2 for x in xs
            )
            projected = ys[-1] + slope * days
        except (OverflowError, ValueError):
            raise InvalidOverrideError(f"Projection of {feature_id!r} does not give a finite value.") from None
        if not math.isfinite(projected):
            raise InvalidOverrideError(f"Projection of {feature_id!r} does not give a finite value.")
        observations[-1][1].value = projected
    return result, unprojected
