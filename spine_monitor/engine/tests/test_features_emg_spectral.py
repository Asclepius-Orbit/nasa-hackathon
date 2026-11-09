"""Familia ``emg_spectral`` (#43): frecuencia mediana del EMG por repetición, su media y su pendiente entre repeticiones.

Rejilla de tiempo de los tests: 1000 Hz (una muestra por ms) desde ``T0``; la muestra ``i`` cae en ``T0 + i ms``.
Las repeticiones se fijan con un ``FakeService`` (frontera). La señal es una senoide de frecuencia conocida o ruido
de banda conocida dentro de cada repetición, y ceros fuera.
"""

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from spine_contracts import FeaturesRequest
from spine_contracts.catalog import expand_template
from spine_contracts.models.domain.feature_set_schema import Reason
from spine_engine.features.context import ExtractionContext, build_context
from spine_engine.features.emg_spectral import EmgSpectralFamily
from spine_engine.features.registry import FamilyResult
from spine_engine.features.repetitions import RepetitionService, RepWindow, RepWindows

_EXAMPLE = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid" / "engine" / "features-request.json"
T0 = datetime(2026, 11, 13, 10, 3, 0, 100000, tzinfo=UTC)
ANCHOR_DEVICE_MS = 180_000
BOOT = 2893746120
BOOT2 = 2893746121
RATE_HZ = 1000.0
NYQUIST_HZ = RATE_HZ / 2.0
MUSCLE = "latissimus_dorsi"
LEFT, RIGHT = "emg_lat_left", "emg_lat_right"
CAL_IDS = {LEFT: "7a1c0d52-8b3e-4f6a-a1d4-5e9b2c7d3f12", RIGHT: "7a1c0d52-8b3e-4f6a-a1d4-5e9b2c7d3f22"}
MEDIAN_LEFT = f"emg.{MUSCLE}.left.median_freq_hz"
SLOPE_LEFT = f"emg.{MUSCLE}.left.median_freq_slope_hz_per_rep"
MEDIAN_RIGHT = f"emg.{MUSCLE}.right.median_freq_hz"
SLOPE_RIGHT = f"emg.{MUSCLE}.right.median_freq_slope_hz_per_rep"
REP_LEN = 1000  # muestras por repetición (1 s): resolución espectral de 1 Hz
GAP = 200
# El PSD de una senoide pura cae en ~3 bins (ventana de Hann): la mediana cae a menos de 2 bins de la frecuencia.
TONE_TOL_HZ = 2.0


def at(samples: int, rate_hz: float = RATE_HZ) -> datetime:
    return T0 + timedelta(milliseconds=samples * 1000.0 / rate_hz)


class FakeService(RepetitionService):
    """Frontera: devuelve unas ventanas fijas, sin mirar el contexto."""

    def __init__(self, windows: tuple[RepWindow, ...]) -> None:
        super().__init__()
        self._result = RepWindows(windows, "events" if windows else "none")

    def windows(self, context: ExtractionContext) -> RepWindows:
        return self._result


def tone(freq_hz: float, n: int = REP_LEN, amplitude: float = 1.0, rate_hz: float = RATE_HZ) -> np.ndarray:
    t = np.arange(n) / rate_hz
    return amplitude * np.sin(2 * np.pi * freq_hz * t)


def band_noise(low_hz: float, high_hz: float, n: int, seed: int = 7) -> np.ndarray:
    spectrum = np.fft.rfft(np.random.default_rng(seed).standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1 / RATE_HZ)
    spectrum[(freqs < low_hz) | (freqs > high_hz)] = 0.0
    return np.fft.irfft(spectrum, n)


def signal_of(reps: list[np.ndarray]) -> tuple[list[float], list[tuple[int, int]]]:
    """Concatena las repeticiones separadas por ceros; devuelve la señal y el [inicio, fin) de cada una en muestras."""
    samples: list[float] = [0.0] * GAP
    spans: list[tuple[int, int]] = []
    for rep in reps:
        spans.append((len(samples), len(samples) + len(rep)))
        samples.extend(float(v) for v in rep)
        samples.extend([0.0] * GAP)
    return samples, spans


def windows_of(spans: list[tuple[int, int]], indexes: list[int] | None = None, rate_hz: float = RATE_HZ) -> tuple[RepWindow, ...]:
    ids = indexes if indexes is not None else list(range(1, len(spans) + 1))
    return tuple(RepWindow(i, at(s, rate_hz), at(e, rate_hz), None, None) for i, (s, e) in zip(ids, spans, strict=True))


def chunks_of(
    stream_id: str, samples: list[float], signal_kind: str, size: int = 500, rate_hz: float = RATE_HZ, reboot_at: int | None = None
) -> list[dict[str, Any]]:
    """Chunks de ``size`` muestras; desde la muestra ``reboot_at`` (múltiplo de ``size``) cambian de ``boot_id``."""
    out: list[dict[str, Any]] = []
    for seq, start in enumerate(range(0, len(samples), size)):
        part = samples[start : start + size]
        out.append(
            {
                "schema_version": "1.0.0",
                "astronaut_id": "AST-01",
                "device_id": "wb-01",
                "stream_id": stream_id,
                "session_id": "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a01",
                "source": "device",
                "boot_id": BOOT2 if reboot_at is not None and start >= reboot_at else BOOT,
                "seq": seq - (reboot_at // size if reboot_at is not None and start >= reboot_at else 0),
                "t0_device_ms": ANCHOR_DEVICE_MS + round(start * 1000.0 / rate_hz),
                "received_at": T0.isoformat(),
                "sample_rate_hz": rate_hz,
                "n_samples": len(part),
                "stream_kind": "emg",
                "signal_kind": signal_kind,
                "units": {"emg": "mV"},
                "data": {"emg": part},
                "quality": {"emg": {"score": 1.0, "flags": [], "assessed_by": "gateway"}},
            }
        )
    return out


def make_context(
    samples: dict[str, list[float]],
    *,
    declared: tuple[str, ...] | None = None,
    signal_kind: str = "raw",
    last_chunk_kind: str | None = None,
    rate_hz: float = RATE_HZ,
    reboot_at: int | None = None,
) -> ExtractionContext:
    payload = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    sides = {LEFT: "left", RIGHT: "right"}
    payload["session"]["streams"] = [
        {
            "stream_id": stream_id,
            "stream_kind": "emg",
            "placement": {"muscle": MUSCLE, "side": sides[stream_id]},
            "sample_rate_hz": rate_hz,
            "calibration_id": CAL_IDS[stream_id],
        }
        for stream_id in (declared if declared is not None else tuple(samples))
    ]
    payload["chunks"] = []
    for stream_id, values in samples.items():
        chunks = chunks_of(stream_id, values, signal_kind, rate_hz=rate_hz, reboot_at=reboot_at)
        if last_chunk_kind is not None:
            chunks[-1]["signal_kind"] = last_chunk_kind
        payload["chunks"].extend(chunks)
    payload["calibrations"] = []
    if reboot_at is not None:  # el segundo arranque tiene su ancla, con el mismo mapa device_ms -> UTC
        payload["clock_syncs"].append({**payload["clock_syncs"][0], "boot_id": BOOT2})
    return build_context(FeaturesRequest.model_validate(payload))


def run(
    reps: list[np.ndarray],
    *,
    indexes: list[int] | None = None,
    signal_kind: str = "raw",
    last_chunk_kind: str | None = None,
    rate_hz: float = RATE_HZ,
    reboot_at: int | None = None,
) -> FamilyResult:
    """Un stream izquierdo con las repeticiones dadas."""
    samples, spans = signal_of(reps)
    context = make_context({LEFT: samples}, signal_kind=signal_kind, last_chunk_kind=last_chunk_kind, rate_hz=rate_hz, reboot_at=reboot_at)
    return EmgSpectralFamily(FakeService(windows_of(spans, indexes, rate_hz))).extract(context)


def values(result: FamilyResult) -> dict[str, float]:
    return {f.feature_id: f.value for f in result.computed}


def reasons(result: FamilyResult) -> dict[str, Reason]:
    return {s.feature_id: s.reason for s in result.skipped}


def test_declares_the_expanded_catalog_ids():
    family = EmgSpectralFamily(RepetitionService())
    expected = (*expand_template("emg.{muscle}.{side}.median_freq_hz"), *expand_template("emg.{muscle}.{side}.median_freq_slope_hz_per_rep"))
    assert family.family == "emg_spectral"
    assert set(family.feature_ids) == set(expected)
    assert MEDIAN_LEFT in family.feature_ids
    assert SLOPE_RIGHT in family.feature_ids


def test_pure_tone_median_is_its_frequency():
    result = run([tone(80.0), tone(80.0)])
    assert values(result)[MEDIAN_LEFT] == pytest.approx(80.0, abs=TONE_TOL_HZ)
    assert not result.skipped


def test_band_limited_noise_median_is_the_band_centre():
    # banda plana 50-150 Hz: la mitad de la potencia queda por debajo de 100 Hz
    result = run([band_noise(50.0, 150.0, 4000), band_noise(50.0, 150.0, 4000, seed=8)])
    assert values(result)[MEDIAN_LEFT] == pytest.approx(100.0, abs=6.0)


def test_two_reps_give_their_mean_and_the_slope_per_rep():
    result = run([tone(100.0), tone(60.0)])
    found = values(result)
    assert found[MEDIAN_LEFT] == pytest.approx(80.0, abs=TONE_TOL_HZ)
    assert found[SLOPE_LEFT] == pytest.approx(-40.0, abs=2 * TONE_TOL_HZ)


def test_slope_sign_follows_the_trend():
    falling = values(run([tone(120.0), tone(100.0), tone(80.0)]))[SLOPE_LEFT]
    rising = values(run([tone(80.0), tone(100.0), tone(120.0)]))[SLOPE_LEFT]
    flat = values(run([tone(90.0), tone(90.0), tone(90.0)]))[SLOPE_LEFT]
    assert falling == pytest.approx(-20.0, abs=TONE_TOL_HZ)
    assert rising == pytest.approx(20.0, abs=TONE_TOL_HZ)
    assert flat == pytest.approx(0.0, abs=1e-9)


def test_slope_is_against_the_repetition_index_not_the_position():
    # índices 1 y 3 (la 2 no está): 40 Hz de diferencia en 2 repeticiones = 20 Hz/rep, como la verdad de terreno
    result = run([tone(100.0), tone(60.0)], indexes=[1, 3])
    assert values(result)[SLOPE_LEFT] == pytest.approx(-20.0, abs=TONE_TOL_HZ)


def test_n_counts_the_valid_repetitions():
    result = run([tone(80.0), tone(80.0), tone(80.0)])
    assert {f.feature_id: f.n for f in result.computed} == {MEDIAN_LEFT: 3, SLOPE_LEFT: 3}
    assert all(f.streams == (LEFT,) for f in result.computed)


def test_one_valid_repetition_is_too_few():
    result = run([tone(80.0)])
    assert not result.computed
    assert reasons(result) == {MEDIAN_LEFT: Reason.TOO_FEW_REPETITIONS, SLOPE_LEFT: Reason.TOO_FEW_REPETITIONS}


def test_no_repetitions_is_too_few():
    samples, _ = signal_of([tone(80.0)])
    result = EmgSpectralFamily(FakeService(())).extract(make_context({LEFT: samples}))
    assert reasons(result) == {MEDIAN_LEFT: Reason.TOO_FEW_REPETITIONS, SLOPE_LEFT: Reason.TOO_FEW_REPETITIONS}


def test_repetition_with_too_few_samples_does_not_count():
    # dos repeticiones buenas y una de 63 muestras a otra frecuencia: ni cuenta en n ni mueve la media
    result = run([tone(80.0), tone(80.0), tone(300.0, 63)])
    assert {f.feature_id: f.n for f in result.computed}[MEDIAN_LEFT] == 2
    assert values(result)[MEDIAN_LEFT] == pytest.approx(80.0, abs=TONE_TOL_HZ)


def test_repetition_with_the_minimum_samples_counts():
    result = run([tone(80.0), tone(80.0, 64)])
    assert {f.feature_id: f.n for f in result.computed}[MEDIAN_LEFT] == 2


def test_a_short_repetition_leaves_too_few_valid_ones():
    result = run([tone(80.0), tone(80.0, 10)])
    assert reasons(result) == {MEDIAN_LEFT: Reason.TOO_FEW_REPETITIONS, SLOPE_LEFT: Reason.TOO_FEW_REPETITIONS}


def test_repetitions_without_samples_are_no_data():
    samples, _ = signal_of([tone(80.0), tone(80.0)])
    far = tuple(RepWindow(i, at(100_000 + 2000 * i), at(101_000 + 2000 * i), None, None) for i in (1, 2))
    result = EmgSpectralFamily(FakeService(far)).extract(make_context({LEFT: samples}))
    assert reasons(result) == {MEDIAN_LEFT: Reason.NO_DATA, SLOPE_LEFT: Reason.NO_DATA}


def test_declared_stream_without_chunks_is_stream_missing():
    samples, spans = signal_of([tone(80.0), tone(80.0)])
    context = make_context({LEFT: samples}, declared=(LEFT, RIGHT))
    result = EmgSpectralFamily(FakeService(windows_of(spans))).extract(context)
    assert reasons(result) == {MEDIAN_RIGHT: Reason.STREAM_MISSING, SLOPE_RIGHT: Reason.STREAM_MISSING}
    assert MEDIAN_LEFT in values(result)


@pytest.mark.parametrize("kind", ["rectified", "envelope"])
def test_non_raw_signal_is_not_raw(kind: str):
    result = run([tone(80.0), tone(80.0)], signal_kind=kind)
    assert not result.computed
    assert reasons(result) == {MEDIAN_LEFT: Reason.SIGNAL_NOT_RAW, SLOPE_LEFT: Reason.SIGNAL_NOT_RAW}


def test_mixing_raw_and_envelope_chunks_is_not_raw():
    result = run([tone(80.0), tone(80.0)], last_chunk_kind="envelope")
    assert reasons(result) == {MEDIAN_LEFT: Reason.SIGNAL_NOT_RAW, SLOPE_LEFT: Reason.SIGNAL_NOT_RAW}


def test_not_raw_takes_precedence_over_repetitions():
    samples, _ = signal_of([tone(80.0)])
    context = make_context({LEFT: samples}, signal_kind="rectified")
    result = EmgSpectralFamily(FakeService(())).extract(context)
    assert reasons(result) == {MEDIAN_LEFT: Reason.SIGNAL_NOT_RAW, SLOPE_LEFT: Reason.SIGNAL_NOT_RAW}


def test_left_and_right_streams_are_independent():
    left, spans = signal_of([tone(100.0), tone(60.0)])
    right, _ = signal_of([tone(150.0), tone(150.0)])
    context = make_context({LEFT: left, RIGHT: right})
    found = values(EmgSpectralFamily(FakeService(windows_of(spans))).extract(context))
    assert found[MEDIAN_LEFT] == pytest.approx(80.0, abs=TONE_TOL_HZ)
    assert found[SLOPE_LEFT] == pytest.approx(-40.0, abs=2 * TONE_TOL_HZ)
    assert found[MEDIAN_RIGHT] == pytest.approx(150.0, abs=TONE_TOL_HZ)
    assert found[SLOPE_RIGHT] == pytest.approx(0.0, abs=1e-9)


def test_non_emg_streams_give_nothing():
    payload = json.loads(_EXAMPLE.read_text(encoding="utf-8"))
    payload["session"]["streams"] = [s for s in payload["session"]["streams"] if s["stream_kind"] != "emg"]
    payload["chunks"] = [c for c in payload["chunks"] if c["stream_kind"] != "emg"]
    context = build_context(FeaturesRequest.model_validate(payload))
    assert EmgSpectralFamily(FakeService(())).extract(context) == FamilyResult((), ())


def test_constant_offset_does_not_move_the_median():
    result = run([tone(80.0) + 5.0, tone(80.0) + 5.0])
    assert values(result)[MEDIAN_LEFT] == pytest.approx(80.0, abs=TONE_TOL_HZ)


def test_silent_signal_gives_zero():
    result = run([np.zeros(REP_LEN), np.zeros(REP_LEN)])
    assert values(result)[MEDIAN_LEFT] == 0.0
    assert values(result)[SLOPE_LEFT] == 0.0


def test_repetition_with_a_nan_sample_is_not_valid():
    bad = tone(300.0)
    bad[10] = np.nan
    result = run([tone(80.0), tone(80.0), bad])
    assert {f.feature_id: f.n for f in result.computed}[MEDIAN_LEFT] == 2
    assert all(math.isfinite(f.value) for f in result.computed)
    assert values(result)[MEDIAN_LEFT] == pytest.approx(80.0, abs=TONE_TOL_HZ)


def test_infinite_sample_makes_a_repetition_invalid_and_leaves_too_few():
    bad = tone(80.0)
    bad[3] = np.inf
    result = run([tone(80.0), bad])
    assert reasons(result) == {MEDIAN_LEFT: Reason.TOO_FEW_REPETITIONS, SLOPE_LEFT: Reason.TOO_FEW_REPETITIONS}


def test_nan_in_every_repetition_is_no_data():
    reps = [tone(80.0), tone(80.0)]
    for rep in reps:
        rep[5] = np.nan
    result = run(reps)
    assert not result.computed
    assert reasons(result) == {MEDIAN_LEFT: Reason.NO_DATA, SLOPE_LEFT: Reason.NO_DATA}


def test_repetition_split_by_a_reboot_uses_its_longest_piece():
    # 300 muestras a 80 Hz (arranque 1) y 700 a 150 Hz (arranque 2): vale el trozo largo; la otra repetición, 150 Hz
    split = np.concatenate([tone(80.0, 300), tone(150.0, 700)])
    result = run([split, tone(150.0)], reboot_at=500)
    assert {f.feature_id: f.n for f in result.computed}[MEDIAN_LEFT] == 2
    assert values(result)[MEDIAN_LEFT] == pytest.approx(150.0, abs=TONE_TOL_HZ)
    assert values(result)[SLOPE_LEFT] == pytest.approx(0.0, abs=TONE_TOL_HZ)


@pytest.mark.parametrize("rate_hz", [500.0, 2000.0])
def test_tone_median_at_other_sample_rates(rate_hz: float):
    rep = tone(80.0, int(rate_hz), rate_hz=rate_hz)  # 1 s: resolución de 1 Hz a cualquier frecuencia de muestreo
    found = values(run([rep, rep], rate_hz=rate_hz))[MEDIAN_LEFT]
    assert found == pytest.approx(80.0, abs=TONE_TOL_HZ)


def test_short_middle_repetition_leaves_a_gap_in_the_slope_index():
    # índices 0, 2, 3; la 2 es corta y se descarta: 100 Hz en el índice 0 y 60 Hz en el 3 -> -40/3 Hz/rep
    result = run([tone(100.0), tone(300.0, 63), tone(60.0)], indexes=[0, 2, 3])
    assert values(result)[SLOPE_LEFT] == pytest.approx(-40.0 / 3.0, abs=TONE_TOL_HZ)
    assert {f.feature_id: f.n for f in result.computed}[SLOPE_LEFT] == 2


def test_repetition_with_the_minimum_samples_has_the_right_value():
    # 64 muestras: segmento de 64, resolución 1000/64 = 15,6 Hz; el lóbulo de Hann abarca ±2 bins = ±31,25 Hz
    result = run([tone(200.0, 64), tone(200.0, 64)])
    assert values(result)[MEDIAN_LEFT] == pytest.approx(200.0, abs=2 * RATE_HZ / 64)


_freq = st.floats(min_value=20.0, max_value=400.0, allow_nan=False, allow_infinity=False)
_amplitude = st.floats(min_value=0.01, max_value=100.0, allow_nan=False, allow_infinity=False)


@pytest.mark.property
@settings(max_examples=40, deadline=None)
@given(freq=_freq)
def test_property_median_of_a_pure_tone_is_its_frequency(freq: float):
    found = values(run([tone(freq), tone(freq)]))[MEDIAN_LEFT]
    assert abs(found - freq) <= TONE_TOL_HZ


@pytest.mark.property
@settings(max_examples=40, deadline=None)
@given(freq=_freq, scale=_amplitude)
def test_property_median_is_scale_invariant(freq: float, scale: float):
    base = values(run([tone(freq), tone(freq)]))[MEDIAN_LEFT]
    scaled = values(run([tone(freq, amplitude=scale), tone(freq, amplitude=scale)]))[MEDIAN_LEFT]
    assert math.isclose(base, scaled, rel_tol=1e-6, abs_tol=1e-6)


@pytest.mark.property
@settings(max_examples=40, deadline=None)
@given(freq=_freq, offset=st.floats(min_value=-50.0, max_value=50.0, allow_nan=False, allow_infinity=False))
def test_property_median_ignores_a_constant_offset(freq: float, offset: float):
    base = values(run([tone(freq), tone(freq)]))[MEDIAN_LEFT]
    shifted = values(run([tone(freq) + offset, tone(freq) + offset]))[MEDIAN_LEFT]
    assert math.isclose(base, shifted, rel_tol=1e-6, abs_tol=1e-6)


@pytest.mark.property
@settings(max_examples=40, deadline=None)
@given(first=_freq, second=_freq)
def test_property_slope_of_two_tones_is_their_frequency_difference(first: float, second: float):
    found = values(run([tone(first), tone(second)]))[SLOPE_LEFT]
    assert abs(found - (second - first)) <= 2 * TONE_TOL_HZ


@pytest.mark.property
@settings(max_examples=40, deadline=None)
@given(freq=_freq, seed=st.integers(min_value=0, max_value=10_000))
def test_property_median_ignores_the_sign(freq: float, seed: int):
    x = tone(freq) + np.random.default_rng(seed).standard_normal(REP_LEN)
    positive = values(run([x, x]))[MEDIAN_LEFT]
    negative = values(run([-x, -x]))[MEDIAN_LEFT]
    assert math.isclose(positive, negative, rel_tol=1e-9, abs_tol=1e-9)
