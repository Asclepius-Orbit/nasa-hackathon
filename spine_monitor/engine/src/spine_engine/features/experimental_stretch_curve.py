"""Modelo exploratorio de cordón; sus resultados no son mediciones clínicas.

Supone resistencia uniforme, contactos despreciables, deformación uniforme y contactos
fijos al material. Devuelve la longitud total equivalente del ensayo o una estimación
del tramo entre contactos; ninguna sustituye una calibración validada del wearable.
"""

import json
from bisect import bisect_left
from collections.abc import Sequence
from functools import cache
from math import isfinite
from statistics import mean

from spine_contracts.catalog import CATALOG_DIR

type Trial = tuple[float, Sequence[float]]


@cache
def measured_trials() -> tuple[float, tuple[Trial, ...]]:
    """Carga las mediciones de banco y separación de contactos del catálogo empaquetado."""
    data = json.loads((CATALOG_DIR / "cord-measurements.json").read_text(encoding="utf-8"))
    bench = data["bench"]
    return float(bench["contact_spacing_mm"]), tuple(
        (float(point["whole_cord_length_mm"]), tuple(float(r) for r in point["resistance_ohm"])) for point in bench["measurements"]
    )


def estimate_equivalent_total_length_mm(
    resistance_ohm: float, trials: Sequence[Trial], bench_contact_span_mm: float, measurement_contact_span_mm: float
) -> float | None:
    """Interpola la longitud total equivalente de ensayo; fuera del dominio retorna ``None``.

    Las lecturas de cada punto se promedian. La resistencia media del tramo de ensayo
    se multiplica por la razón entre las separaciones de contactos, suponiendo que
    la resistencia por unidad de longitud es uniforme y la de contacto despreciable.
    """
    if not all(isfinite(v) and v > 0 for v in (bench_contact_span_mm, measurement_contact_span_mm)):
        raise ValueError("contact spans must be finite and positive")
    if not isfinite(resistance_ohm) or resistance_ohm <= 0:
        raise ValueError("resistance must be finite and positive")
    if len(trials) < 2:
        raise ValueError("at least two trial lengths are required")
    ratio = measurement_contact_span_mm / bench_contact_span_mm
    if not isfinite(ratio) or ratio <= 0:
        raise ValueError("contact span ratio is not representable")
    points = []
    for length, readings in trials:
        if not isfinite(length) or length <= 0 or not readings or any(not isfinite(r) or r <= 0 for r in readings):
            raise ValueError("trial lengths and readings must be finite and positive")
        scaled = mean(readings) * ratio
        if not isfinite(scaled) or scaled <= 0:
            raise ValueError("scaled trial resistance is not representable")
        points.append((scaled, length))
    points.sort(key=lambda point: point[1])
    if any(a[0] >= b[0] or a[1] >= b[1] for a, b in zip(points, points[1:], strict=False)):
        raise ValueError("trial lengths and mean resistances must both increase strictly")
    resistances = [point[0] for point in points]
    index = bisect_left(resistances, resistance_ohm)
    if index == len(points) or (index == 0 and resistance_ohm < resistances[0]):
        return None
    if resistance_ohm == resistances[index]:
        return points[index][1]
    r0, length0 = points[index - 1]
    r1, length1 = points[index]
    return length0 + (resistance_ohm - r0) * (length1 - length0) / (r1 - r0)


def estimate_contact_span_mm(
    resistance_ohm: float, straight_resistance_ohm: float, trials: Sequence[Trial], bench_contact_span_mm: float, straight_span_mm: float
) -> float | None:
    """Estima el tramo actual suponiendo deformación uniforme y contactos fijos al material."""
    baseline = estimate_equivalent_total_length_mm(straight_resistance_ohm, trials, bench_contact_span_mm, straight_span_mm)
    current = estimate_equivalent_total_length_mm(resistance_ohm, trials, bench_contact_span_mm, straight_span_mm)
    if baseline is None or current is None:
        return None
    result = straight_span_mm * (current / baseline)
    if not isfinite(result) or result <= 0:
        raise ValueError("estimated span is not representable")
    return result
