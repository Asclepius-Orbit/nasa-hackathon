"""Modelo de riesgo genérico sobre las desviaciones: sin rangos clínicos ni sentido de riesgo por feature."""

import math
from collections.abc import Mapping, Sequence

from spine_contracts import AssessmentDomain, Driver

from spine_engine.interfaces import Deviation, RiskResult
from spine_engine.pipeline import domain_of


class DeviationRiskModel:
    """Implementa `RiskModel`. Combina el |z| de las desviaciones sostenidas.

    Cada desviación sostenida aporta `|z| · peso de su dominio · persistencia`, con la persistencia
    `min(n_points, persistence_points) / persistence_points`. La suma se satura a [0, 1) con
    `1 - exp(-suma / saturation)`. Sin `risk_direction` conocido, cualquier sentido es peor: se usa
    |z|, y el signo solo viaja en `deviation_z` del driver. Una desviación aislada, sin z o de una
    feature sin dominio no aporta riesgo. La confianza es la completitud: lo que falta no la sube.
    """

    def __init__(self, domain_weights: Mapping[AssessmentDomain, float], persistence_points: int, saturation: float) -> None:
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

    def _raw(self, deviation: Deviation) -> float:
        domain = domain_of(deviation.feature_id)
        if not deviation.sustained or deviation.z is None or domain is None:
            return 0.0
        persistence = min(deviation.n_points, self._persistence_points) / self._persistence_points
        return abs(deviation.z) * self._weights.get(domain, 0.0) * persistence

    def score(self, deviations: Sequence[Deviation], completeness: float) -> RiskResult:
        raw = [(d, c) for d in deviations if (c := self._raw(d)) > 0]
        total = sum(c for _, c in raw)
        score = 1 - math.exp(-total / self._saturation)
        drivers = [
            Driver(feature_id=d.feature_id, value=d.value, deviation_z=d.z, contribution=score * c / total)
            for d, c in sorted(raw, key=lambda item: (-item[1], item[0].feature_id))
        ]
        return RiskResult(score=score, drivers=drivers, confidence=completeness)
