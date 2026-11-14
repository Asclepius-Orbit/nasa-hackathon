"""Modelo de riesgo genérico sobre las desviaciones: sin rangos clínicos ni sentido de riesgo por feature."""

import math
from collections.abc import Mapping, Sequence
from typing import Any, Final, Protocol

from spine_contracts import AssessmentDomain, Driver

from spine_engine.interfaces import Deviation, RiskResult
from spine_engine.pipeline import domain_of

# ASSUMPTION: provisional weights, pending Aguado. Multiplier of a driver's contribution by the level of the
# catalog range its value falls in. Alert keeps the generic weight; a value that is clinically normal is
# damped, a critical one is amplified. A value in no range, or a feature without ranges, uses 1.0.
RANGE_LEVEL_WEIGHTS: Final[Mapping[str, float]] = {"normal": 0.5, "alert": 1.0, "critical": 2.0}
_WORST_FIRST: Final = ("critical", "alert", "normal")


class CatalogView(Protocol):
    """Lo que el modelo lee del catálogo de features. Lanza `KeyError` con un id que no conoce."""

    def risk_direction(self, feature_id: str) -> str: ...

    def ranges(self, feature_id: str) -> dict[str, Any] | None: ...


def _within(bounds: Mapping[str, float | None], value: float) -> bool:
    lo, hi = bounds.get("min"), bounds.get("max")
    return (lo is None or value >= lo) and (hi is None or value <= hi)


def range_level(ranges: Mapping[str, Any], value: float) -> str | None:
    """Nivel (critical, alert o normal) del rango que contiene `value`; extremos inclusivos, gana el peor."""
    for level in _WORST_FIRST:
        bounds = ranges.get(level)
        if bounds is not None and _within(bounds, value):
            return level
    return None


class DeviationRiskModel:
    """Implementa `RiskModel`. Combina el |z| de las desviaciones sostenidas.

    Cada desviación sostenida aporta `|z| · peso de su dominio · persistencia`, con la persistencia
    `min(n_points, persistence_points) / persistence_points`. La suma se satura a [0, 1] con
    `1 - exp(-suma / saturation)`, calculada con `expm1` para no perder sumas diminutas (con sumas muy
    grandes el redondeo llega a 1.0). Sin `risk_direction` conocido, cualquier sentido es peor: se usa
    |z|, y el signo solo viaja en `deviation_z` del driver. Una desviación aislada, sin z o de una
    feature sin dominio no aporta riesgo. La confianza es la completitud: lo que falta no la sube.

    Con un catálogo (`CatalogView`), el sentido de riesgo de la feature filtra la desviación: `lower_is_worse`
    solo cuenta si baja, `higher_is_worse` solo si sube, `none` nunca cuenta y `either_is_worse` o
    `unspecified` usan |z|. El sentido es `Deviation.direction` y, si falta, el signo de z. Si la feature
    tiene rangos, el nivel donde cae su valor (extremos inclusivos, el peor gana en un extremo compartido)
    multiplica la contribución con `RANGE_LEVEL_WEIGHTS`; un valor fuera de todo rango o una feature sin
    rangos se queda en el modelo genérico de z. Un id que el catálogo no conoce también. Sin catálogo
    (por defecto) nada de esto aplica.

    Limitación: `n_points` es el largo de la serie de la feature, no el tramo final desviado, y los
    detectores exigen un mínimo de puntos para marcar una desviación como sostenida. Con
    `persistence_points` menor o igual a ese mínimo el factor de persistencia vale siempre 1.
    Un dominio ausente de `domain_weights` no aporta riesgo (`EngineSettings` los completa).
    """

    def __init__(
        self,
        domain_weights: Mapping[AssessmentDomain, float],
        persistence_points: int,
        saturation: float,
        catalog: CatalogView | None = None,
    ) -> None:
        if persistence_points < 1:
            raise ValueError("persistence_points must be at least 1")
        if not math.isfinite(saturation):
            raise ValueError("saturation must be finite")
        if saturation <= 0:
            raise ValueError("saturation must be positive")
        if not all(math.isfinite(weight) for weight in domain_weights.values()):
            raise ValueError("domain weights must be finite")
        if any(weight < 0 for weight in domain_weights.values()):
            raise ValueError("domain weights must not be negative")
        self._weights = dict(domain_weights)
        self._persistence_points = persistence_points
        self._saturation = saturation
        self._catalog = catalog

    def _catalog_weight(self, deviation: Deviation, z: float) -> float:
        """Factor del catálogo sobre la contribución: 0 si la desviación va en el sentido bueno."""
        if self._catalog is None:
            return 1.0
        try:
            direction = self._catalog.risk_direction(deviation.feature_id)
            ranges = self._catalog.ranges(deviation.feature_id)
        except KeyError:
            return 1.0
        moved = deviation.direction or ("down" if z < 0 else "up")
        if direction == "none" or (direction == "lower_is_worse" and moved != "down") or (direction == "higher_is_worse" and moved != "up"):
            return 0.0
        level = None if ranges is None else range_level(ranges, deviation.value)
        return 1.0 if level is None else RANGE_LEVEL_WEIGHTS[level]

    def _raw(self, deviation: Deviation) -> float:
        domain = domain_of(deviation.feature_id)
        if not deviation.sustained or deviation.z is None or domain is None:
            return 0.0
        persistence = min(deviation.n_points, self._persistence_points) / self._persistence_points
        return abs(deviation.z) * self._weights.get(domain, 0.0) * persistence * self._catalog_weight(deviation, deviation.z)

    def score(self, deviations: Sequence[Deviation], completeness: float) -> RiskResult:
        raw = [(d, c) for d in deviations if (c := self._raw(d)) > 0]
        total = sum(c for _, c in raw)
        score = -math.expm1(-total / self._saturation)
        drivers = [
            Driver(feature_id=d.feature_id, value=d.value, deviation_z=d.z, contribution=score * c / total)
            for d, c in sorted(raw, key=lambda item: (-item[1], item[0].feature_id))
        ]
        return RiskResult(score=score, drivers=drivers, confidence=completeness)
