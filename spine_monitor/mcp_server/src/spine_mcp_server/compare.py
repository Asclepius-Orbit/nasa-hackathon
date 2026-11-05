"""Cálculo puro de la salida de la herramienta MCP `compare_periods` (contrato `mcp/compare-periods`).

Recibe los dos historiales ya leídos (periodo A y periodo B) y no hace I/O. Todo delta es B menos A.
"""

from collections import Counter, defaultdict
from statistics import fmean, median
from typing import Any

from spine_contracts import SCHEMA_VERSION, Assessment, ComparePeriods, History, Level

# Clave de comparación: solo son comparables valores de la misma feature, protocolo y gravedad.
type Key = tuple[str, str, str]

# Gravedad de cada nivel, para desempatar `latest` cuando dos assessments comparten `as_of`: gana el peor.
_SEVERITY = {Level.GREEN: 0, Level.AMBER: 1, Level.RED: 2}


def compare_periods(a: History, b: History) -> ComparePeriods:
    """Compara dos ventanas del historial de un mismo astronauta.

    Lanza ValueError si los historiales son de astronautas distintos.
    """
    if a.astronaut_id != b.astronaut_id:
        raise ValueError(f"cannot compare histories of different astronauts: {a.astronaut_id} vs {b.astronaut_id}")
    values_a, values_b = _values_by_key(a), _values_by_key(b)
    common = sorted(values_a.keys() & values_b.keys())
    # Una clave con más de una unidad (entre periodos o dentro de uno) no es comparable: fuera de features y aviso.
    mismatched = [key for key in common if len(values_a[key].keys() | values_b[key].keys()) > 1]
    features = [_comparison(key, values_a[key], values_b[key]) for key in common if key not in mismatched]
    warnings = [
        name
        for name, active in (
            ("period_a_empty", _is_empty(a)),
            ("period_b_empty", _is_empty(b)),
            ("period_a_truncated", a.truncated),
            ("period_b_truncated", b.truncated),
            ("unit_mismatch", bool(mismatched)),
        )
        if active
    ]
    return ComparePeriods.model_validate(
        {
            "schema_version": SCHEMA_VERSION,
            "astronaut_id": a.astronaut_id,
            "period_a": _period(a),
            "period_b": _period(b),
            "features": features,
            "only_in_a": [_key_dict(k) for k in sorted(values_a.keys() - values_b.keys())],
            "only_in_b": [_key_dict(k) for k in sorted(values_b.keys() - values_a.keys())],
            "levels": {"a": _levels(a.assessments), "b": _levels(b.assessments)},
            "scores": {
                "risk_score": _score([x.risk_score for x in a.assessments], [x.risk_score for x in b.assessments]),
                "confidence": _score([x.confidence for x in a.assessments], [x.confidence for x in b.assessments]),
                "completeness": _score([x.completeness.score for x in a.assessments], [x.completeness.score for x in b.assessments]),
            },
            "warnings": warnings,
        }
    )


def _values_by_key(history: History) -> dict[Key, dict[str, list[float]]]:
    """Valores de cada clave agrupados por unidad.

    Los feature sets sin protocolo o sin gravedad (p. ej. ventanas móviles de síntomas) no tienen clave
    comparable y se ignoran; siguen contando en `feature_sets_count`.
    """
    grouped: dict[Key, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for feature_set in history.feature_sets:
        if feature_set.protocol_id is None or feature_set.gravity_environment is None:
            continue
        for feature in feature_set.features:
            key = (feature.feature_id, feature_set.protocol_id, feature_set.gravity_environment.value)
            grouped[key][feature.unit].append(feature.value)
    return grouped


def _key_dict(key: Key) -> dict[str, str]:
    feature_id, protocol_id, gravity_environment = key
    return {"feature_id": feature_id, "protocol_id": protocol_id, "gravity_environment": gravity_environment}


def _comparison(key: Key, by_unit_a: dict[str, list[float]], by_unit_b: dict[str, list[float]]) -> dict[str, Any]:
    """Entrada de `features` para una clave con la misma y única unidad en ambos periodos."""
    (unit,) = by_unit_a
    stats_a, stats_b = _stats(by_unit_a[unit]), _stats(by_unit_b[unit])
    return _key_dict(key) | {
        "unit": unit,
        "a": stats_a,
        "b": stats_b,
        "delta_mean": stats_b["mean"] - stats_a["mean"],
        "delta_median": stats_b["median"] - stats_a["median"],
    }


def _stats(values: list[float]) -> dict[str, Any]:
    return {"n": len(values), "mean": fmean(values), "median": median(values)}


def _period(history: History) -> dict[str, Any]:
    return {
        "from": history.from_,
        "to": history.to,
        "assessments_count": len(history.assessments),
        "feature_sets_count": len(history.feature_sets),
        "truncated": history.truncated,
    }


def _is_empty(history: History) -> bool:
    return not history.assessments and not history.feature_sets


def _levels(assessments: list[Assessment]) -> dict[str, Any]:
    """Recuento por nivel y nivel del assessment más reciente (el peor si empatan en `as_of`)."""
    counts = Counter(x.level for x in assessments)
    latest = max(assessments, key=lambda x: (x.as_of, _SEVERITY[x.level]), default=None)
    return {
        "green": counts[Level.GREEN],
        "amber": counts[Level.AMBER],
        "red": counts[Level.RED],
        "latest": None if latest is None else latest.level,
    }


def _score(values_a: list[float], values_b: list[float]) -> dict[str, float | None]:
    """Media por periodo (null sin assessments) y delta b - a (null si falta un lado)."""
    mean_a = fmean(values_a) if values_a else None
    mean_b = fmean(values_b) if values_b else None
    delta = None if mean_a is None or mean_b is None else mean_b - mean_a
    return {"a": mean_a, "b": mean_b, "delta": delta}
