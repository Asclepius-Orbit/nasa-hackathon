"""Ayudantes privados de ``repetitions_imu`` (#41) con entradas hechas a mano y salidas exactas.

Se importan los privados a propósito: cada uno tiene aritmética de índices y desempates (umbrales ``>=``/``>``, distancia
exacta de fusión, orden de emparejado) que los tests de extremo a extremo con señales suaves no distinguen.
"""

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
import spine_contracts
from spine_contracts import ClockSync, FeaturesRequest, ImuChunk
from spine_engine.features.clock import DeviceClock
from spine_engine.features.context import ExtractionContext, build_context
from spine_engine.features.ordering import Segment
from spine_engine.features.repetitions_imu import (
    MIN_PEAK_SEPARATION_S,
    _descend,
    _dominant_axis,
    _Lobe,
    _lobes,
    _merge_close,
    _noise_sigma,
    _pairs,
    _segment_spans,
    _smooth,
    _windows,
    detect_imu_windows,
)

# vía el paquete instalado, no vía parents[] desde los tests: bajo mutmut los tests se copian a otro árbol
_EXAMPLE = Path(spine_contracts.__file__).resolve().parents[2] / "examples" / "valid" / "engine" / "features-request.json"
T0 = datetime(2026, 11, 13, 10, 0, 0, tzinfo=UTC)
BOOT = 7


def lobe(first: int, last: int, sign: int = 1, peak: int | None = None, value: float = 1.0) -> _Lobe:
    return _Lobe(first, last, sign, first if peak is None else peak, value)


def pad(*parts: list[float], zeros: int = 5) -> list[float]:
    """Ceros, tramo, ceros, tramo... (el exceso de ceros deja la mediana de |Δ| en 0: sin suelo de ruido)."""
    out: list[float] = [0.0] * zeros
    for part in parts:
        out += part + [0.0] * zeros
    return out


# --- _smooth ---------------------------------------------------------------------------------------


def test_smooth_averages_a_centred_window_and_shrinks_it_at_the_edges():
    assert _smooth([0.0, 0.0, 3.0, 0.0, 0.0], 3) == [0.0, 1.0, 1.0, 1.0, 0.0]
    assert _smooth([3.0, 0.0, 0.0, 0.0, 3.0], 3) == [1.5, 1.0, 0.0, 1.0, 1.5]
    assert _smooth([1.0, 2.0, 3.0, 4.0, 5.0], 5) == [2.0, 2.5, 3.0, 3.5, 4.0]


def test_smooth_with_width_one_is_the_identity_and_an_even_width_acts_as_the_next_odd_one():
    signal = [1.0, -2.0, 4.0, 0.5]
    assert _smooth(signal, 1) == signal
    assert _smooth(signal, 2) == _smooth(signal, 3)
    assert _smooth([], 3) == []


# --- _noise_sigma ----------------------------------------------------------------------------------


def test_noise_sigma_is_the_scaled_median_of_the_absolute_first_differences():
    expected = 1.4826 * 2.0 / math.sqrt(2.0)
    assert _noise_sigma([0.0, 1.0, 3.0, 6.0]) == pytest.approx(expected, rel=1e-12)
    # diferencias negativas: sin el valor absoluto la mediana sería -2
    assert _noise_sigma([6.0, 3.0, 1.0, 0.0]) == pytest.approx(expected, rel=1e-12)
    assert _noise_sigma([0.0, 2.0]) == pytest.approx(1.4826 * 2.0 / math.sqrt(2.0), rel=1e-12)


def test_noise_sigma_is_zero_without_differences_or_with_a_flat_signal():
    assert _noise_sigma([]) == 0.0
    assert _noise_sigma([5.0]) == 0.0
    assert _noise_sigma([3.0, 3.0, 3.0]) == 0.0


# --- _descend --------------------------------------------------------------------------------------


def test_descend_follows_the_tail_while_the_magnitude_keeps_falling():
    smoothed = [1.0, 2.0, 3.0, 5.0, 4.0, 2.0, 1.0, 0.0, -1.0]
    assert _descend(smoothed, 3, 1, 1) == 6  # 4, 2, 1 y se para ante el 0
    assert _descend(smoothed, 3, -1, 1) == 0  # llega al índice 0 (borde inferior incluido)


def test_descend_stops_on_a_plateau_a_rise_or_a_sign_change():
    assert _descend([3.0, 3.0, 2.0], 0, 1, 1) == 0  # no estrictamente decreciente
    assert _descend([5.0, 3.0, 4.0], 0, 1, 1) == 1  # sube después
    assert _descend([5.0, -3.0, -1.0], 0, 1, 1) == 0  # cambia de signo aunque |ω| baje
    assert _descend([5.0, 0.0, 0.0], 0, 1, 1) == 0  # cero no es del signo


def test_descend_handles_negative_lobes_and_the_upper_bound():
    assert _descend([-1.0, -3.0, -5.0, -2.0, -1.0], 2, 1, -1) == 4
    assert _descend([-1.0, -3.0, -5.0, -2.0, -1.0], 2, -1, -1) == 0
    assert _descend([1.0, 2.0], 1, 1, 1) == 1  # no sale por arriba


# --- _merge_close ----------------------------------------------------------------------------------


def test_merge_close_keeps_lobes_whose_peaks_are_exactly_the_minimum_separation_apart():
    a, b = lobe(0, 2, peak=1), lobe(3, 6, peak=4)
    assert _merge_close([a, b], 3.0) == [a, b]  # 4 - 1 == 3: no se funden
    assert _merge_close([a, b], 3.0001) == [_Lobe(0, 6, 1, 1, 1.0)]


def test_merge_close_joins_edges_and_keeps_the_stronger_peak():
    a = _Lobe(0, 2, 1, 1, 5.0)
    b = _Lobe(3, 6, 1, 4, 9.0)
    assert _merge_close([a, b], 10.0) == [_Lobe(0, 6, 1, 4, 9.0)]
    assert _merge_close([_Lobe(0, 2, 1, 1, 9.0), _Lobe(3, 6, 1, 4, 5.0)], 10.0) == [_Lobe(0, 6, 1, 1, 9.0)]


def test_merge_close_prefers_the_earlier_peak_on_equal_strength():
    assert _merge_close([_Lobe(0, 2, 1, 1, 4.0), _Lobe(3, 6, 1, 4, 4.0)], 10.0) == [_Lobe(0, 6, 1, 1, 4.0)]


def test_merge_close_never_fuses_opposite_signs_and_compares_against_the_merged_peak():
    plus, minus = lobe(0, 1, 1), lobe(2, 3, -1)
    assert _merge_close([plus, minus, lobe(4, 5, 1)], 100.0) == [plus, minus, lobe(4, 5, 1)]
    # a (pico 0, fuerte) + b (pico 2) se funden en pico 0; c (pico 3) dista 3 >= 2.5 del fundido (y 1 < 2.5 de b)
    a, b, c = _Lobe(0, 1, 1, 0, 5.0), _Lobe(2, 3, 1, 2, 4.0), _Lobe(4, 5, 1, 3, 3.0)
    assert _merge_close([a, b, c], 2.5) == [_Lobe(0, 3, 1, 0, 5.0), c]
    assert _merge_close([], 2.5) == []


# --- _pairs ----------------------------------------------------------------------------------------


def test_pairs_needs_an_out_lobe_followed_by_an_opposite_one():
    assert _pairs([]) == []
    assert _pairs([lobe(0, 1, 1)]) == []
    assert _pairs([lobe(0, 1, 1), lobe(2, 3, 1)]) == []
    out, back = lobe(0, 1, 1), lobe(2, 3, -1)
    assert _pairs([out, back]) == [(out, back)]
    out, back = lobe(0, 1, -1), lobe(2, 3, 1)
    assert _pairs([out, back]) == [(out, back)]


def test_pairs_takes_the_flexion_sign_that_gives_more_pairs():
    p = [lobe(0, 1, 1), lobe(2, 3, -1), lobe(4, 5, -1), lobe(6, 7, 1), lobe(8, 9, -1)]
    assert _pairs(p) == [(p[0], p[1]), (p[3], p[4])]
    m = [lobe(0, 1, -1), lobe(2, 3, 1), lobe(4, 5, 1), lobe(6, 7, -1), lobe(8, 9, 1)]
    assert _pairs(m) == [(m[0], m[1]), (m[3], m[4])]


def test_pairs_does_not_reuse_a_lobe_in_two_pairs():
    ls = [lobe(0, 1, 1), lobe(2, 3, -1), lobe(4, 5, 1), lobe(6, 7, -1), lobe(8, 9, 1)]
    assert _pairs(ls) == [(ls[0], ls[1]), (ls[2], ls[3])]  # +1 da 2 pares; -1 da 2 con más hueco o igual: gana +1


def test_pairs_breaks_a_tie_on_count_with_the_smaller_total_gap():
    # +,-,+ : con +1 el par (L0,L1); con -1 el par (L1,L2). Gana el de menor hueco, sea cual sea el signo
    small_after = [lobe(0, 1, 1), lobe(10, 11, -1), lobe(12, 13, 1)]
    assert _pairs(small_after) == [(small_after[1], small_after[2])]
    small_before = [lobe(0, 1, 1), lobe(2, 3, -1), lobe(12, 13, 1)]
    assert _pairs(small_before) == [(small_before[0], small_before[1])]


def test_pairs_adds_the_gaps_of_every_pair_when_comparing():
    # +1: (0,1) y (2,3) con huecos 1 y 1 -> 2; -1: (1,2) y (3,4) con huecos 5 y 0 -> 5: gana +1
    ls = [lobe(0, 1, 1), lobe(2, 3, -1), lobe(4, 5, 1), lobe(5, 6, -1), lobe(11, 12, 1)]
    assert _pairs(ls) == [(ls[0], ls[1]), (ls[2], ls[3])]
    # simétrico: el signo -1 suma menos
    ms = [lobe(0, 1, 1), lobe(6, 7, -1), lobe(7, 8, 1), lobe(12, 13, -1), lobe(13, 14, 1)]
    assert _pairs(ms) == [(ms[1], ms[2]), (ms[3], ms[4])]


def test_pairs_prefers_plus_one_when_count_and_gap_tie():
    ls = [lobe(0, 1, 1), lobe(3, 4, -1), lobe(6, 7, 1)]  # huecos 2 y 2
    assert _pairs(ls) == [(ls[0], ls[1])]


# --- _dominant_axis --------------------------------------------------------------------------------


def test_dominant_axis_is_none_without_finite_samples_or_without_energy():
    assert _dominant_axis([[], [], []]) is None
    assert _dominant_axis([[math.nan, math.inf], [], []]) is None
    assert _dominant_axis([[1.0, 1.0, 1.0], [0.0, 0.0], [2.0]]) is None  # constantes: tras restar la mediana, energía 0


def test_dominant_axis_returns_the_median_centred_axis_with_most_energy():
    assert _dominant_axis([[10.0, 10.0, 10.0, 12.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 0.0, 0.0]]) == [0.0, 0.0, 0.0, 2.0]
    assert _dominant_axis([[0.0, 1.0, 0.0], [0.0, 0.0, 5.0], [0.0, 2.0, 0.0]]) == [0.0, 0.0, 5.0]
    assert _dominant_axis([[0.0, 1.0, 0.0], [0.0, 3.0, 0.0], [0.0, 2.0, 0.0]]) == [0.0, 3.0, 0.0]


def test_dominant_axis_ties_go_to_the_first_axis_and_non_finite_samples_become_rest():
    assert _dominant_axis([[0.0, 2.0, 0.0], [0.0, -2.0, 0.0], [0.0, 0.0, 2.0]]) == [0.0, 2.0, 0.0]
    assert _dominant_axis([[0.0, 0.0, 0.0], [0.0, -2.0, 0.0], [0.0, 2.0, 0.0]]) == [0.0, -2.0, 0.0]
    assert _dominant_axis([[10.0, math.nan, 10.0, 12.0, math.inf, 10.0], [], []]) == [0.0, 0.0, 0.0, 2.0, 0.0, 0.0]
    assert _dominant_axis([[], [math.nan], [0.0, 4.0, 0.0]]) == [0.0, 4.0, 0.0]


# --- _windows --------------------------------------------------------------------------------------


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def test_windows_number_from_one_and_rest_runs_to_the_next_start_except_for_the_last():
    assert _windows([]) == ()
    one, two, three = _windows([(at(1), at(2)), (at(4), at(5)), (at(8), at(9))])
    assert (one.index, two.index, three.index) == (1, 2, 3)
    assert (one.start, one.end) == (at(1), at(2))
    assert (one.rest_start, one.rest_end) == (at(2), at(4))
    assert (two.rest_start, two.rest_end) == (at(5), at(8))
    assert (three.rest_start, three.rest_end) == (None, None)
    (only,) = _windows([(at(1), at(2))])
    assert (only.index, only.rest_start, only.rest_end) == (1, None, None)


# --- _lobes ----------------------------------------------------------------------------------------
# A 10 Hz la media móvil mide 1 muestra (identidad) y la separación mínima son 2,5 muestras.

RATE = 10.0


def test_lobes_finds_one_lobe_per_run_with_exact_edges_peak_and_sign():
    signal = pad([2.0, 4.0, 2.0], [-2.0, -4.0, -2.0])
    assert _lobes(signal, RATE) == [_Lobe(5, 7, 1, 6, 4.0), _Lobe(13, 15, -1, 14, 4.0)]


def test_lobes_edges_grow_along_the_falling_tail_below_the_low_threshold():
    # pico 100; bajo = 3. Las colas 0,5 1 2 quedan bajo el umbral pero bajan sin cambiar de signo: el borde las recupera
    signal = pad([0.5, 1.0, 2.0, 50.0, 100.0, 50.0, 2.0, 1.0, 0.5], zeros=12)
    assert _lobes(signal, RATE) == [_Lobe(12, 20, 1, 16, 100.0)]
    # una cola que vuelve a subir no se sigue: 2, 1 y el 2 final queda fuera
    assert _lobes(pad([100.0, 2.0, 1.0, 2.0], zeros=12), RATE) == [_Lobe(12, 14, 1, 12, 100.0)]


def test_lobes_scales_down_to_sub_unit_amplitudes():
    # amplitudes < 1: ni el signo ni los umbrales dependen de valores absolutos de 1
    assert _lobes(pad([0.125, 0.25, 0.125], [-0.125, -0.25, -0.125], zeros=12), RATE) == [_Lobe(12, 14, 1, 13, 0.25), _Lobe(27, 29, -1, 28, 0.25)]


def test_lobes_start_and_end_of_the_signal_are_valid_lobe_bounds():
    signal = [4.0, 2.0] + [0.0] * 12 + [2.0, 4.0]
    assert _lobes(signal, RATE) == [_Lobe(0, 1, 1, 0, 4.0), _Lobe(14, 15, 1, 15, 4.0)]


@pytest.mark.parametrize("zeros", [4, 5])
def test_lobes_a_lobe_whose_first_sample_is_its_peak_keeps_that_sample(zeros: int):
    # con cualquier paridad de ceros previos: saltar de dos en dos pasaría por encima del primer índice del lóbulo
    assert _lobes(pad([4.0, 2.0], zeros=zeros), RATE) == [_Lobe(zeros, zeros + 1, 1, zeros, 4.0)]


def test_lobes_run_to_the_last_above_threshold_sample_even_with_an_even_length():
    assert _lobes(pad([2.0, 4.0, 4.0, 2.0], zeros=12), RATE) == [_Lobe(12, 15, 1, 13, 4.0)]
    # el máximo está en la última muestra antes de caer a cero
    assert _lobes(pad([1.0, 2.0, 4.0], zeros=12), RATE) == [_Lobe(12, 14, 1, 14, 4.0)]


def test_lobes_split_exactly_where_the_sign_changes_even_when_the_next_lobe_starts_at_its_peak():
    # [2 4 2 | -4 -2]: sin hueco; el segundo lóbulo empieza en su pico
    assert _lobes(pad([2.0, 4.0, 2.0, -4.0, -2.0], zeros=12), RATE) == [_Lobe(12, 14, 1, 13, 4.0), _Lobe(15, 16, -1, 15, 4.0)]
    # positivo que sube hacia un cambio de signo, y negativo seguido de positivo
    assert _lobes(pad([2.0, 4.0, -2.0, -4.0], zeros=12), RATE) == [_Lobe(12, 13, 1, 13, 4.0), _Lobe(14, 15, -1, 15, 4.0)]
    assert _lobes(pad([-2.0, -4.0, -2.0, 2.0, 4.0, 2.0], zeros=12), RATE) == [_Lobe(12, 14, -1, 13, 4.0), _Lobe(15, 17, 1, 16, 4.0)]


def test_lobes_the_peak_is_taken_inside_the_lobe_only():
    # tras el lóbulo positivo hay un negativo mucho mayor: no puede ser el pico del positivo
    found = _lobes(pad([2.0, 4.0, 2.0, -8.0, -4.0], zeros=12), RATE)
    assert [(lb.sign, lb.peak, lb.peak_value) for lb in found] == [(1, 13, 4.0), (-1, 15, 8.0)]


def test_lobes_peak_threshold_is_inclusive_and_a_lower_peak_is_discarded():
    # pico global 100 -> alto = 25: un lóbulo de pico exactamente 25 cuenta; de 24 no
    base = pad([50.0, 100.0, 50.0], zeros=8)
    inclusive = base + pad([25.0])[5:]
    assert [lb.peak_value for lb in _lobes(inclusive, RATE)] == [100.0, 25.0]
    discarded = base + pad([24.0])[5:]
    assert [lb.peak_value for lb in _lobes(discarded, RATE)] == [100.0]


def test_lobes_the_global_maximum_sets_the_relative_threshold():
    # un lóbulo de pico 30 sobrevive junto a uno de 100 (30 >= 25) pero no junto a uno de 200 (30 < 50)
    first = pad([30.0], [100.0], zeros=8)
    assert [lb.peak_value for lb in _lobes(first, RATE)] == [30.0, 100.0]
    second = pad([30.0], [200.0], zeros=8)
    assert [lb.peak_value for lb in _lobes(second, RATE)] == [200.0]


def test_lobes_a_sample_equal_to_the_low_threshold_ends_the_lobe():
    # bajo = 0,03 * 100 = 3: con muestras == 3 no se sigue el tramo; el segundo lóbulo (50) es otro
    signal = pad([100.0, 3.0, 3.0, 3.0, 50.0])
    assert _lobes(signal, RATE) == [_Lobe(5, 6, 1, 5, 100.0), _Lobe(8, 9, 1, 9, 50.0)]


def test_lobes_a_sign_change_without_a_gap_starts_a_new_lobe():
    signal = pad([2.0, 4.0, 2.0, -2.0, -4.0, -2.0])
    assert _lobes(signal, RATE) == [_Lobe(5, 7, 1, 6, 4.0), _Lobe(8, 10, -1, 9, 4.0)]


def test_lobes_a_negative_only_signal_gives_negative_lobes():
    assert _lobes(pad([-1.0, -4.0, -1.0]), RATE) == [_Lobe(5, 7, -1, 6, 4.0)]


def test_lobes_merges_same_sign_peaks_closer_than_the_minimum_separation_but_not_at_it():
    # picos a 2 muestras (2 < 2,5): un lóbulo; a 3 muestras: dos
    near = pad([4.0, 0.0, 3.0])
    assert _lobes(near, RATE) == [_Lobe(5, 7, 1, 5, 4.0)]
    far = pad([4.0, 0.0, 0.0, 3.0])
    assert [lb.peak for lb in _lobes(far, RATE)] == [5, 8]
    assert MIN_PEAK_SEPARATION_S * RATE == 2.5


def test_lobes_noise_floor_raises_the_peak_threshold_above_the_relative_one():
    # señal alternante ±1 (sigma estimada alta) con un lóbulo de pico 10: alto = max(2,5; 8*sigma) deja fuera todo
    noisy = [(-1.0) ** i for i in range(20)]
    noisy[10] = 10.0
    sigma = _noise_sigma(noisy)
    assert 8.0 * sigma > 0.25 * 10.0
    assert _lobes(noisy, RATE) == []


def test_lobes_flat_or_empty_signals_give_nothing():
    assert _lobes([0.0] * 10, RATE) == []


def test_lobes_smooths_with_an_odd_window_of_about_a_tenth_of_a_second():
    # 40 Hz -> ventana de 5 muestras (round(4)|1): un pulso de 1 muestra y altura 10 queda en 10/5 = 2, simétrico
    rate = 40.0
    signal = [0.0] * 20 + [10.0] + [0.0] * 20 + [-10.0] + [0.0] * 20
    found = _lobes(signal, rate)
    assert [(lb.first, lb.last, lb.sign, lb.peak) for lb in found] == [(18, 22, 1, 18), (39, 43, -1, 39)]
    assert [lb.peak_value for lb in found] == [pytest.approx(2.0), pytest.approx(2.0)]


# --- _segment_spans --------------------------------------------------------------------------------


def _chunk(t0_device_ms: int, seq: int, rate: float, gyro: dict[str, list[float]]) -> ImuChunk:
    n = len(next(iter(gyro.values())))
    channels = {name: gyro.get(name, [0.0] * n) for name in ("gyro_x", "gyro_y", "gyro_z")}
    raw: dict[str, Any] = {
        "schema_version": "1.0.0",
        "astronaut_id": "AST-01",
        "device_id": "wb-01",
        "stream_id": "imu_t9",
        "session_id": "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01",
        "source": "device",
        "boot_id": BOOT,
        "seq": seq,
        "t0_device_ms": t0_device_ms,
        "received_at": T0.isoformat(),
        "sample_rate_hz": rate,
        "n_samples": n,
        "stream_kind": "imu",
        "units": {"accel_x": "g", "accel_y": "g", "accel_z": "g", "gyro_x": "deg/s", "gyro_y": "deg/s", "gyro_z": "deg/s"},
        "data": {"accel_x": [0.0] * n, "accel_y": [0.0] * n, "accel_z": [0.0] * n, **channels},
        "quality": {"accel": {"score": 1.0, "flags": [], "assessed_by": "gateway"}, "gyro": {"score": 1.0, "flags": [], "assessed_by": "gateway"}},
    }
    return ImuChunk.model_validate(raw)


_SYNC: dict[str, Any] = {
    "schema_version": "1.0.0",
    "astronaut_id": "AST-01",
    "device_id": "wb-01",
    "boot_id": BOOT,
    "device_ms": 1000,
    "utc": T0.isoformat(),
    "uncertainty_ms": 5,
    "method": "round_trip",
}


class _Context:
    """Frontera mínima: ``_segment_spans`` solo usa el reloj del contexto."""

    def __init__(self) -> None:
        self.clock = DeviceClock.from_syncs([ClockSync.model_validate(_SYNC)])


def _segment(*chunks: ImuChunk) -> Segment:
    return Segment("imu_t9", BOOT, tuple(chunks), sum(c.n_samples for c in chunks), 0)


def _spans(*chunks: ImuChunk) -> list[tuple[datetime, datetime]]:
    return _segment_spans(cast("ExtractionContext", _Context()), _segment(*chunks))


def test_segment_spans_maps_sample_indices_to_rounded_utc_milliseconds():
    # 6 Hz: paso 166,67 ms. Ida en 4..6, vuelta en 10..12. Inicio: 1000 + 4*166,67 = 1666,67 -> 1667 -> +667 ms.
    # Fin: fin de la muestra 12 = 1000 + 13*166,67 = 3166,67 -> 3167 -> +2167 ms (round, no floor)
    signal = [0.0] * 4 + [2.0, 4.0, 2.0] + [0.0] * 3 + [-2.0, -4.0, -2.0] + [0.0] * 5
    assert _spans(_chunk(1000, 0, 6.0, {"gyro_z": signal, "gyro_x": [0.1] * len(signal)})) == [
        (T0 + timedelta(milliseconds=667), T0 + timedelta(milliseconds=2167))
    ]


def test_segment_spans_uses_each_chunks_own_start_time_across_gaps():
    # 10 Hz; el segundo chunk empieza 1 s tarde (huecos de reloj): el fin sale de su t0, no de la continuidad del índice
    signal = pad([2.0, 4.0, 2.0], [-2.0, -4.0, -2.0])  # ida 5..7, vuelta 13..15; n = 21
    first, second = _chunk(1000, 0, 10.0, {"gyro_y": signal[:10]}), _chunk(3000, 1, 10.0, {"gyro_y": signal[10:]})
    # inicio: muestra 5 del chunk 1 -> 1500 ms; fin: muestra 15 = índice 5 del chunk 2 -> 3500 + 100 -> 3600
    assert _spans(first, second) == [(T0 + timedelta(milliseconds=500), T0 + timedelta(milliseconds=2600))]


def test_segment_spans_gives_one_span_per_pair_and_nothing_for_a_quiet_or_empty_segment():
    signal = pad([2.0, 4.0, 2.0], [-2.0, -4.0, -2.0], [2.0, 4.0, 2.0], [-2.0, -4.0, -2.0])
    assert len(_spans(_chunk(1000, 0, 10.0, {"gyro_y": signal}))) == 2
    assert _spans(_chunk(1000, 0, 10.0, {"gyro_y": [0.0] * 20})) == []
    assert _segment_spans(cast("ExtractionContext", _Context()), _segment()) == []


def test_pairs_scans_on_one_lobe_after_a_mismatch():
    ls = [lobe(0, 1, 1), lobe(2, 3, 1), lobe(4, 5, -1)]
    assert _pairs(ls) == [(ls[1], ls[2])]
    mixed = [lobe(0, 1, 1), lobe(2, 3, -1), lobe(4, 5, -1), lobe(6, 7, 1), lobe(8, 9, -1)]
    assert _pairs(mixed)[-1] == (mixed[3], mixed[4])


def test_dominant_axis_keeps_axes_whose_energy_is_below_one():
    assert _dominant_axis([[0.0, 0.1, 0.0], [], []]) == [0.0, 0.1, 0.0]


def test_segment_spans_without_a_clock_anchor_for_the_boot_gives_nothing():
    signal = pad([2.0, 4.0, 2.0], [-2.0, -4.0, -2.0])
    chunk = _chunk(1000, 0, 10.0, {"gyro_y": signal})
    segment = Segment("imu_t9", BOOT + 1, (chunk,), len(signal), 0)
    assert _segment_spans(cast("ExtractionContext", _Context()), segment) == []


def test_detect_imu_windows_skips_a_declared_stream_without_chunks_and_uses_the_next_one():
    raw = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    signal = pad([2.0, 4.0, 2.0], [-2.0, -4.0, -2.0])
    chunk = _chunk(1000, 0, 10.0, {"gyro_y": signal}).model_dump(mode="json")
    raw["chunks"] = [c for c in raw["chunks"] if c["stream_kind"] != "imu"] + [chunk]
    raw["clock_syncs"] = [_SYNC]
    raw["session"]["events"] = []
    declared = next(s for s in raw["session"]["streams"] if s["stream_id"] == "imu_t9")
    # "imu_a0" ordena antes que "imu_t9", está declarado y no trae chunks
    raw["session"]["streams"].append({**declared, "stream_id": "imu_a0"})
    context = build_context(FeaturesRequest.model_validate(raw))

    assert context.stream_missing("imu_a0")
    (window,) = detect_imu_windows(context)
    assert window.start == T0 + timedelta(milliseconds=500)


def test_segment_spans_end_is_exactly_one_sample_after_the_last_sample():
    # 3 Hz: paso 333,33 ms. Inicio: 1000 + 4*333,33 = 2333,33 -> 2333 (+1333 ms). Fin: 1000 + 13*333,33 = 5333,33 -> 5333
    # (+4333 ms); con un paso algo mayor (1001/rate) el fin redondearía a 5334
    signal = [0.0] * 4 + [2.0, 4.0, 2.0] + [0.0] * 3 + [-2.0, -4.0, -2.0] + [0.0] * 5
    assert _spans(_chunk(1000, 0, 3.0, {"gyro_z": signal})) == [(T0 + timedelta(milliseconds=1333), T0 + timedelta(milliseconds=4333))]


def test_lobes_keep_a_flat_low_tail_whose_samples_are_each_below_the_peak_threshold_and_one():
    # pico 0,5: alto 0,125 y bajo 0,015; las colas de 0,0625 no pasan solas el umbral alto pero forman parte del lóbulo
    assert _lobes(pad([0.0625, 0.0625, 0.5, 0.0625, 0.0625], zeros=12), RATE) == [_Lobe(12, 16, 1, 14, 0.5)]
