"""Private helpers of ``repetitions_imu`` (#41) with hand-made inputs and exact outputs.

The privates are imported on purpose: each has index arithmetic and tie-breaks (``>=``/``>`` thresholds, exact merge
distance, pairing order) that end-to-end tests with smooth signals do not distinguish.
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

# via the installed package, not via parents[] from the tests: under mutmut the tests are copied to another tree
_EXAMPLE = Path(spine_contracts.__file__).resolve().parents[2] / "examples" / "valid" / "engine" / "features-request.json"
T0 = datetime(2026, 11, 13, 10, 0, 0, tzinfo=UTC)
BOOT = 7


def lobe(first: int, last: int, sign: int = 1, peak: int | None = None, value: float = 1.0) -> _Lobe:
    return _Lobe(first, last, sign, first if peak is None else peak, value)


def pad(*parts: list[float], zeros: int = 5) -> list[float]:
    """Zeros, run, zeros, run... (the excess of zeros leaves the median of |Δ| at 0: no noise floor)."""
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
    # negative differences: without the absolute value the median would be -2
    assert _noise_sigma([6.0, 3.0, 1.0, 0.0]) == pytest.approx(expected, rel=1e-12)
    assert _noise_sigma([0.0, 2.0]) == pytest.approx(1.4826 * 2.0 / math.sqrt(2.0), rel=1e-12)


def test_noise_sigma_is_zero_without_differences_or_with_a_flat_signal():
    assert _noise_sigma([]) == 0.0
    assert _noise_sigma([5.0]) == 0.0
    assert _noise_sigma([3.0, 3.0, 3.0]) == 0.0


# --- _descend --------------------------------------------------------------------------------------


def test_descend_follows_the_tail_while_the_magnitude_keeps_falling():
    smoothed = [1.0, 2.0, 3.0, 5.0, 4.0, 2.0, 1.0, 0.0, -1.0]
    assert _descend(smoothed, 3, 1, 1) == 6  # 4, 2, 1 and it stops at the 0
    assert _descend(smoothed, 3, -1, 1) == 0  # reaches index 0 (lower edge included)


def test_descend_stops_on_a_plateau_a_rise_or_a_sign_change():
    assert _descend([3.0, 3.0, 2.0], 0, 1, 1) == 0  # no estrictamente decreciente
    assert _descend([5.0, 3.0, 4.0], 0, 1, 1) == 1  # rises afterwards
    assert _descend([5.0, -3.0, -1.0], 0, 1, 1) == 0  # cambia de signo aunque |ω| baje
    assert _descend([5.0, 0.0, 0.0], 0, 1, 1) == 0  # zero is not of the sign


def test_descend_handles_negative_lobes_and_the_upper_bound():
    assert _descend([-1.0, -3.0, -5.0, -2.0, -1.0], 2, 1, -1) == 4
    assert _descend([-1.0, -3.0, -5.0, -2.0, -1.0], 2, -1, -1) == 0
    assert _descend([1.0, 2.0], 1, 1, 1) == 1  # does not exit from above


# --- _merge_close ----------------------------------------------------------------------------------


def test_merge_close_keeps_lobes_whose_peaks_are_exactly_the_minimum_separation_apart():
    a, b = lobe(0, 2, peak=1), lobe(3, 6, peak=4)
    assert _merge_close([a, b], 3.0) == [a, b]  # 4 - 1 == 3: they are not merged
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
    # a (peak 0, strong) + b (peak 2) merge into peak 0; c (peak 3) is 3 >= 2.5 from the merged one (and 1 < 2.5 from b)
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
    assert _pairs(ls) == [(ls[0], ls[1]), (ls[2], ls[3])]  # +1 gives 2 pairs; -1 gives 2 with a larger or equal gap: +1 wins


def test_pairs_breaks_a_tie_on_count_with_the_smaller_total_gap():
    # +,-,+ : with +1 the pair (L0,L1); with -1 the pair (L1,L2). The smaller gap wins, whatever the sign
    small_after = [lobe(0, 1, 1), lobe(10, 11, -1), lobe(12, 13, 1)]
    assert _pairs(small_after) == [(small_after[1], small_after[2])]
    small_before = [lobe(0, 1, 1), lobe(2, 3, -1), lobe(12, 13, 1)]
    assert _pairs(small_before) == [(small_before[0], small_before[1])]


def test_pairs_adds_the_gaps_of_every_pair_when_comparing():
    # +1: (0,1) and (2,3) with gaps 1 and 1 -> 2; -1: (1,2) and (3,4) with gaps 5 and 0 -> 5: +1 wins
    ls = [lobe(0, 1, 1), lobe(2, 3, -1), lobe(4, 5, 1), lobe(5, 6, -1), lobe(11, 12, 1)]
    assert _pairs(ls) == [(ls[0], ls[1]), (ls[2], ls[3])]
    # symmetric: sign -1 adds less
    ms = [lobe(0, 1, 1), lobe(6, 7, -1), lobe(7, 8, 1), lobe(12, 13, -1), lobe(13, 14, 1)]
    assert _pairs(ms) == [(ms[1], ms[2]), (ms[3], ms[4])]


def test_pairs_prefers_plus_one_when_count_and_gap_tie():
    ls = [lobe(0, 1, 1), lobe(3, 4, -1), lobe(6, 7, 1)]  # huecos 2 y 2
    assert _pairs(ls) == [(ls[0], ls[1])]


# --- _dominant_axis --------------------------------------------------------------------------------


def test_dominant_axis_is_none_without_finite_samples_or_without_energy():
    assert _dominant_axis([[], [], []]) is None
    assert _dominant_axis([[math.nan, math.inf], [], []]) is None
    assert _dominant_axis([[1.0, 1.0, 1.0], [0.0, 0.0], [2.0]]) is None  # constants: after subtracting the median, energy 0


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
# At 10 Hz the moving average measures 1 sample (identity) and the minimum separation is 2.5 samples.

RATE = 10.0


def test_lobes_finds_one_lobe_per_run_with_exact_edges_peak_and_sign():
    signal = pad([2.0, 4.0, 2.0], [-2.0, -4.0, -2.0])
    assert _lobes(signal, RATE) == [_Lobe(5, 7, 1, 6, 4.0), _Lobe(13, 15, -1, 14, 4.0)]


def test_lobes_edges_grow_along_the_falling_tail_below_the_low_threshold():
    # peak 100; low = 3. The tails 0.5 1 2 fall under the threshold but descend without changing sign: the edge recovers them
    signal = pad([0.5, 1.0, 2.0, 50.0, 100.0, 50.0, 2.0, 1.0, 0.5], zeros=12)
    assert _lobes(signal, RATE) == [_Lobe(12, 20, 1, 16, 100.0)]
    # a tail that rises again is not followed: 2, 1 and the final 2 is left out
    assert _lobes(pad([100.0, 2.0, 1.0, 2.0], zeros=12), RATE) == [_Lobe(12, 14, 1, 12, 100.0)]


def test_lobes_scales_down_to_sub_unit_amplitudes():
    # amplitudes < 1: neither the sign nor the thresholds depend on absolute values of 1
    assert _lobes(pad([0.125, 0.25, 0.125], [-0.125, -0.25, -0.125], zeros=12), RATE) == [_Lobe(12, 14, 1, 13, 0.25), _Lobe(27, 29, -1, 28, 0.25)]


def test_lobes_start_and_end_of_the_signal_are_valid_lobe_bounds():
    signal = [4.0, 2.0] + [0.0] * 12 + [2.0, 4.0]
    assert _lobes(signal, RATE) == [_Lobe(0, 1, 1, 0, 4.0), _Lobe(14, 15, 1, 15, 4.0)]


@pytest.mark.parametrize("zeros", [4, 5])
def test_lobes_a_lobe_whose_first_sample_is_its_peak_keeps_that_sample(zeros: int):
    # with any parity of preceding zeros: stepping by two would skip over the lobe's first index
    assert _lobes(pad([4.0, 2.0], zeros=zeros), RATE) == [_Lobe(zeros, zeros + 1, 1, zeros, 4.0)]


def test_lobes_run_to_the_last_above_threshold_sample_even_with_an_even_length():
    assert _lobes(pad([2.0, 4.0, 4.0, 2.0], zeros=12), RATE) == [_Lobe(12, 15, 1, 13, 4.0)]
    # the maximum is at the last sample before dropping to zero
    assert _lobes(pad([1.0, 2.0, 4.0], zeros=12), RATE) == [_Lobe(12, 14, 1, 14, 4.0)]


def test_lobes_split_exactly_where_the_sign_changes_even_when_the_next_lobe_starts_at_its_peak():
    # [2 4 2 | -4 -2]: no gap; the second lobe starts at its peak
    assert _lobes(pad([2.0, 4.0, 2.0, -4.0, -2.0], zeros=12), RATE) == [_Lobe(12, 14, 1, 13, 4.0), _Lobe(15, 16, -1, 15, 4.0)]
    # positive rising towards a sign change, and negative followed by positive
    assert _lobes(pad([2.0, 4.0, -2.0, -4.0], zeros=12), RATE) == [_Lobe(12, 13, 1, 13, 4.0), _Lobe(14, 15, -1, 15, 4.0)]
    assert _lobes(pad([-2.0, -4.0, -2.0, 2.0, 4.0, 2.0], zeros=12), RATE) == [_Lobe(12, 14, -1, 13, 4.0), _Lobe(15, 17, 1, 16, 4.0)]


def test_lobes_the_peak_is_taken_inside_the_lobe_only():
    # after the positive lobe there is a much larger negative: it cannot be the positive's peak
    found = _lobes(pad([2.0, 4.0, 2.0, -8.0, -4.0], zeros=12), RATE)
    assert [(lb.sign, lb.peak, lb.peak_value) for lb in found] == [(1, 13, 4.0), (-1, 15, 8.0)]


def test_lobes_peak_threshold_is_inclusive_and_a_lower_peak_is_discarded():
    # global peak 100 -> high = 25: a lobe with peak exactly 25 counts; one with 24 does not
    base = pad([50.0, 100.0, 50.0], zeros=8)
    inclusive = base + pad([25.0])[5:]
    assert [lb.peak_value for lb in _lobes(inclusive, RATE)] == [100.0, 25.0]
    discarded = base + pad([24.0])[5:]
    assert [lb.peak_value for lb in _lobes(discarded, RATE)] == [100.0]


def test_lobes_the_global_maximum_sets_the_relative_threshold():
    # a lobe with peak 30 survives next to one of 100 (30 >= 25) but not next to one of 200 (30 < 50)
    first = pad([30.0], [100.0], zeros=8)
    assert [lb.peak_value for lb in _lobes(first, RATE)] == [30.0, 100.0]
    second = pad([30.0], [200.0], zeros=8)
    assert [lb.peak_value for lb in _lobes(second, RATE)] == [200.0]


def test_lobes_a_sample_equal_to_the_low_threshold_ends_the_lobe():
    # low = 0.03 * 100 = 3: with samples == 3 the run is not followed; the second lobe (50) is another
    signal = pad([100.0, 3.0, 3.0, 3.0, 50.0])
    assert _lobes(signal, RATE) == [_Lobe(5, 6, 1, 5, 100.0), _Lobe(8, 9, 1, 9, 50.0)]


def test_lobes_a_sign_change_without_a_gap_starts_a_new_lobe():
    signal = pad([2.0, 4.0, 2.0, -2.0, -4.0, -2.0])
    assert _lobes(signal, RATE) == [_Lobe(5, 7, 1, 6, 4.0), _Lobe(8, 10, -1, 9, 4.0)]


def test_lobes_a_negative_only_signal_gives_negative_lobes():
    assert _lobes(pad([-1.0, -4.0, -1.0]), RATE) == [_Lobe(5, 7, -1, 6, 4.0)]


def test_lobes_merges_same_sign_peaks_closer_than_the_minimum_separation_but_not_at_it():
    # peaks 2 samples apart (2 < 2.5): one lobe; 3 samples apart: two
    near = pad([4.0, 0.0, 3.0])
    assert _lobes(near, RATE) == [_Lobe(5, 7, 1, 5, 4.0)]
    far = pad([4.0, 0.0, 0.0, 3.0])
    assert [lb.peak for lb in _lobes(far, RATE)] == [5, 8]
    assert MIN_PEAK_SEPARATION_S * RATE == 2.5


def test_lobes_noise_floor_raises_the_peak_threshold_above_the_relative_one():
    # alternating signal ±1 (high estimated sigma) with a lobe of peak 10: high = max(2.5; 8*sigma) leaves everything out
    noisy = [(-1.0) ** i for i in range(20)]
    noisy[10] = 10.0
    sigma = _noise_sigma(noisy)
    assert 8.0 * sigma > 0.25 * 10.0
    assert _lobes(noisy, RATE) == []


def test_lobes_flat_or_empty_signals_give_nothing():
    assert _lobes([0.0] * 10, RATE) == []


def test_lobes_smooths_with_an_odd_window_of_about_a_tenth_of_a_second():
    # 40 Hz -> 5-sample window (round(4)|1): a 1-sample pulse of height 10 ends up at 10/5 = 2, symmetric
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
    """Minimal boundary: ``_segment_spans`` only uses the context clock."""

    def __init__(self) -> None:
        self.clock = DeviceClock.from_syncs([ClockSync.model_validate(_SYNC)])


def _segment(*chunks: ImuChunk) -> Segment:
    return Segment("imu_t9", BOOT, tuple(chunks), sum(c.n_samples for c in chunks), 0)


def _spans(*chunks: ImuChunk) -> list[tuple[datetime, datetime]]:
    return _segment_spans(cast("ExtractionContext", _Context()), _segment(*chunks))


def test_segment_spans_maps_sample_indices_to_rounded_utc_milliseconds():
    # 6 Hz: paso 166,67 ms. Ida en 4..6, vuelta en 10..12. Inicio: 1000 + 4*166,67 = 1666,67 -> 1667 -> +667 ms.
    # End: end of sample 12 = 1000 + 13*166.67 = 3166.67 -> 3167 -> +2167 ms (round, not floor)
    signal = [0.0] * 4 + [2.0, 4.0, 2.0] + [0.0] * 3 + [-2.0, -4.0, -2.0] + [0.0] * 5
    assert _spans(_chunk(1000, 0, 6.0, {"gyro_z": signal, "gyro_x": [0.1] * len(signal)})) == [
        (T0 + timedelta(milliseconds=667), T0 + timedelta(milliseconds=2167))
    ]


def test_segment_spans_uses_each_chunks_own_start_time_across_gaps():
    # 10 Hz; the second chunk starts 1 s late (clock gaps): the end comes from its t0, not from index continuity
    signal = pad([2.0, 4.0, 2.0], [-2.0, -4.0, -2.0])  # ida 5..7, vuelta 13..15; n = 21
    first, second = _chunk(1000, 0, 10.0, {"gyro_y": signal[:10]}), _chunk(3000, 1, 10.0, {"gyro_y": signal[10:]})
    # start: sample 5 of chunk 1 -> 1500 ms; end: sample 15 = index 5 of chunk 2 -> 3500 + 100 -> 3600
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
    # "imu_a0" sorts before "imu_t9", is declared and brings no chunks
    raw["session"]["streams"].append({**declared, "stream_id": "imu_a0"})
    context = build_context(FeaturesRequest.model_validate(raw))

    assert context.stream_missing("imu_a0")
    (window,) = detect_imu_windows(context)
    assert window.start == T0 + timedelta(milliseconds=500)


def test_segment_spans_end_is_exactly_one_sample_after_the_last_sample():
    # 3 Hz: paso 333,33 ms. Inicio: 1000 + 4*333,33 = 2333,33 -> 2333 (+1333 ms). Fin: 1000 + 13*333,33 = 5333,33 -> 5333
    # (+4333 ms); with a slightly larger step (1001/rate) the end would round to 5334
    signal = [0.0] * 4 + [2.0, 4.0, 2.0] + [0.0] * 3 + [-2.0, -4.0, -2.0] + [0.0] * 5
    assert _spans(_chunk(1000, 0, 3.0, {"gyro_z": signal})) == [(T0 + timedelta(milliseconds=1333), T0 + timedelta(milliseconds=4333))]


def test_lobes_keep_a_flat_low_tail_whose_samples_are_each_below_the_peak_threshold_and_one():
    # peak 0.5: high 0.125 and low 0.015; the 0.0625 tails do not pass the high threshold alone but are part of the lobe
    assert _lobes(pad([0.0625, 0.0625, 0.5, 0.0625, 0.0625], zeros=12), RATE) == [_Lobe(12, 16, 1, 14, 0.5)]
