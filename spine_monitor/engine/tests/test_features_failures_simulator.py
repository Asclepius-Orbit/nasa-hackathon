"""Casos de fallo de extremo a extremo (#49): sesiones del simulador degradadas a propósito y lo que hace el extractor.

Cada caso parte de una sesión normal del simulador (todos los streams, ``CompositeFeatureExtractor(default_registry())``) y
le quita o le cambia una cosa; lo que sale debe ser una omisión con el motivo correcto (``skipped``) o, cuando el motor
documenta otra cosa, esa otra cosa, y nunca una feature inventada. Ya cubiertos contra la salida del simulador en otros
ficheros (no se repiten aquí):

- sin calibración MVC (``no_calibration``) y calibración caducada en EMG: ``test_features_emg_simulator.py``;
- EMG como envolvente (``signal_not_raw``) en las espectrales: ``test_features_emg_spectral_simulator.py`` (la amplitud no
  exige señal cruda: ``test_features_emg_simulator.py`` comprueba que da lo mismo);
- IMU de fondo a 10 Hz sin ``insufficient_sample_rate``: ``test_features_imu_simulator.py`` (aquí se añade la sesión de
  ejercicio a 10 Hz contra la verdad).

Lo que el motor hace de verdad, y se fija aquí tal cual:

- el extractor nunca emite ``insufficient_sample_rate``: con la IMU a 10 Hz calcula las features, algo peor;
- con huecos de ``seq`` (``lost_chunks``) las features se calculan y la calidad lo dice (``packet_gap`` y puntuación =
  cobertura); no se omiten por ello.

Los ayudantes de sesión se cargan por ruta desde ``test_features_stretch_simulator.py``: con ``--import-mode=importlib``
los tests no se importan entre sí por nombre.
"""

import importlib.util
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from spine_contracts import FeaturesRequest
from spine_engine.features.ordering import IncidentKind, order_chunks

_SPEC = importlib.util.spec_from_file_location("_stretch_simulator", Path(__file__).with_name("test_features_stretch_simulator.py"))
assert _SPEC is not None and _SPEC.loader is not None
_SIM: Any = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_SIM)

Scenario = _SIM.Scenario
ALL_STREAMS: tuple[str, ...] = _SIM.ALL_STREAMS
SEEDS: list[int] = _SIM.SEEDS
GRAVITIES: list[str] = _SIM.GRAVITIES
EXERCISE: str = _SIM.EXERCISE
STRETCH: str = _SIM.STRETCH
PER_REP_REL: float = _SIM.PER_REP_REL
SYMMETRY = "emg.latissimus_dorsi.symmetry_index_pct"
IMU_ROM = "imu.t9.sagittal.rom_deg"
IMU_PEAK = "imu.t9.sagittal.peak_angular_velocity_dps"
STRETCH_PEAK = f"{_SIM.PREFIX}.peak_elongation_mm"

# IMU a 10 Hz (sesión de ejercicio): la ROM sale de integrar la velocidad y apenas pierde; observado +0,3 % (semilla 7) y
# +0,1 % (20261113) sobre la verdad; umbral 2 %. El pico del giróscopo sí se subestima al muestrear 10x menos:
# observado -1,7 % y -1,5 %; umbral 5 %.
IMU_10HZ_ROM_REL = 0.02
IMU_10HZ_PEAK_REL = 0.05
IMU_10HZ = 10.0
# Pérdida de chunks: probabilidad alta para que haya huecos de sobra (el 15 % de los chunks de IMU y cordón en las dos
# semillas) frente al 0,2 % de la misión de ejemplo.
PACKET_LOSS = 0.05
# Con huecos la puntuación de calidad es la cobertura = chunks presentes / chunks esperados entre el primero y el último
# recibidos (tamaño de chunk constante): coincide con la cuenta de chunks hasta el redondeo (observado 0 en las 4 combinaciones).
COVERAGE_ABS = 1e-6
# Con el 15 % de huecos la ROM de la IMU baja (-5,3 % y -6,2 % observado) porque se integra con huecos; umbral 15 %.
IMU_ROM_LOSS_REL = 0.15
GAP_STREAMS = ("imu_t9", STRETCH)


# Los casos que solo miran qué se omite y por qué no dependen del entorno de gravedad ni de la semilla (la omisión sale de
# las calibraciones y de los streams, no de la señal) y se comparan con la sesión completa de la misma configuración: una
# sola semilla y gravedad, y chunks de 1 s, porque extraer una sesión completa tarda ~10 s. Los casos que comparan con la
# verdad numérica (IMU a 10 Hz, huecos de seq) mantienen los dos semillas, la gravedad de la Tierra (el modelo de señal
# no cambia con ella) y los 100 ms de la misión.
STRUCTURAL_SEEDS = [7]
STRUCTURAL_GRAVITY = "earth_1g"
STRUCTURAL_CHUNK_MS = 1000
TRUTH_GRAVITY = "earth_1g"


def _simulate(*, seed: int, gravity: str, **scenario: Any) -> Any:
    streams = scenario.pop("streams", ALL_STREAMS)
    return _SIM._simulate(Scenario(streams=streams, gravity=gravity, **scenario), seed, EXERCISE)


@cache
def _full_features(seed: int) -> dict[str, dict[str, Any]]:
    """Features de la sesión completa y sin degradar (la referencia con la que se comparan los casos estructurales)."""
    return _SIM._by_id(_extract(_simulate(seed=seed, gravity=STRUCTURAL_GRAVITY, chunk_ms=STRUCTURAL_CHUNK_MS).payload))


def _extract(payload: dict[str, Any]) -> dict[str, Any]:
    return _SIM._extract(payload)


def _chunks_of(payload: dict[str, Any], stream_id: str) -> list[dict[str, Any]]:
    return [c for c in payload["chunks"] if c["stream_id"] == stream_id]


def _quality_of(features: dict[str, dict[str, Any]], feature_id: str) -> dict[str, Any]:
    return features[feature_id]["quality"]


# (calibración que se quita, ids que se omiten como no_calibration, ids de la familia que se siguen calculando)
_NO_CALIBRATION = {
    "stretch": (
        "stretch",
        {f"{_SIM.PREFIX}.{n}" for n in ("elongation_mm", "elongation_pct", "peak_elongation_mm", "range_mm", "recovery_time_s")},
        {f"{_SIM.PREFIX}.rest_resistance_ohm"},
    ),
    "imu": (
        "imu",
        {f"imu.t9.{p}.{n}" for p in ("sagittal", "frontal", "transverse") for n in ("rom_deg", "peak_angular_velocity_dps")} | {"imu.t9.accel_rms_g"},
        set(),
    ),
}


@pytest.mark.integration
@pytest.mark.parametrize("seed", STRUCTURAL_SEEDS)
@pytest.mark.parametrize("family", sorted(_NO_CALIBRATION))
def test_without_the_calibration_of_a_family_its_calibrated_features_are_skipped_and_nothing_is_invented(family: str, seed: int):
    calibration_type, skipped_ids, kept_ids = _NO_CALIBRATION[family]
    sim = _simulate(seed=seed, gravity=STRUCTURAL_GRAVITY, chunk_ms=STRUCTURAL_CHUNK_MS)
    full = _full_features(seed)
    assert calibration_type in {c["calibration_type"] for c in sim.payload["calibrations"]}
    payload = {**sim.payload, "calibrations": [c for c in sim.payload["calibrations"] if c["calibration_type"] != calibration_type]}

    result = _extract(payload)

    features = _SIM._by_id(result)
    assert _SIM._skipped(result) == dict.fromkeys(skipped_ids, "no_calibration")
    assert not skipped_ids & set(features)
    assert kept_ids <= set(features)
    # lo que no depende de esa calibración sale idéntico a la sesión completa
    unaffected = set(full) - skipped_ids
    assert set(features) == unaffected
    assert {i: features[i]["value"] for i in unaffected} == {i: full[i]["value"] for i in unaffected}


# (stream que falta, ids que deben omitirse como stream_missing)
_MISSING_STREAM: dict[str, Callable[[str], bool]] = {
    "emg_lat_left": lambda i: i.startswith("emg.") and (".left." in i or i == SYMMETRY),
    "emg_lat_right": lambda i: i.startswith("emg.") and (".right." in i or i == SYMMETRY),
    "imu_t9": lambda i: i.startswith("imu."),
    STRETCH: lambda i: i.startswith("stretch."),
}


@pytest.mark.integration
@pytest.mark.parametrize("seed", STRUCTURAL_SEEDS)
@pytest.mark.parametrize("stream_id", sorted(_MISSING_STREAM))
def test_an_absent_stream_skips_exactly_its_features_as_stream_missing_and_leaves_the_rest_untouched(stream_id: str, seed: int):
    sim = _simulate(seed=seed, gravity=STRUCTURAL_GRAVITY, chunk_ms=STRUCTURAL_CHUNK_MS)
    full = _full_features(seed)
    affected = {i for i in full if _MISSING_STREAM[stream_id](i)}
    assert affected  # la sesión completa sí las calculaba
    payload = {**sim.payload, "chunks": [c for c in sim.payload["chunks"] if c["stream_id"] != stream_id]}

    result = _extract(payload)

    features = _SIM._by_id(result)
    assert {i for i, r in _SIM._skipped(result).items() if r == "stream_missing"} == affected
    assert set(_SIM._skipped(result)) == affected
    assert not affected & set(features)
    assert set(features) == set(full) - affected
    # exercise sale de los eventos de la sesión (no de ningún stream) y los demás streams no se ven afectados
    for feature_id in set(features):
        if feature_id.startswith(("exercise.", "stretch.", "imu.")) and not _MISSING_STREAM[stream_id](feature_id):
            assert features[feature_id]["value"] == full[feature_id]["value"]


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
def test_the_imu_at_10_hz_is_never_skipped_for_its_sample_rate_and_stays_close_to_the_truth(seed: int):
    gravity = TRUTH_GRAVITY
    # El extractor no tiene motivo insufficient_sample_rate en la práctica: con 10 Hz calcula, y bien.
    sim = _simulate(seed=seed, gravity=gravity, streams=("imu_t9",), imu_rate_hz=IMU_10HZ)
    assert {c["sample_rate_hz"] for c in _chunks_of(sim.payload, "imu_t9")} == {IMU_10HZ}

    result = _extract(sim.payload)

    features = _SIM._by_id(result)
    assert "insufficient_sample_rate" not in set(_SIM._skipped(result).values())
    assert not [i for i in _SIM._skipped(result) if i.startswith("imu.")]
    assert features[IMU_ROM]["value"] == pytest.approx(sim.truth[IMU_ROM], rel=IMU_10HZ_ROM_REL)
    assert features[IMU_PEAK]["value"] == pytest.approx(sim.truth[IMU_PEAK], rel=IMU_10HZ_PEAK_REL)
    assert features[IMU_ROM]["n"] == features[IMU_PEAK]["n"] == sim.truth["exercise.rep_count"]


def _lost_by_stream(sim: Any) -> dict[str, list[tuple[int, int]]]:
    streams = sim.records["lost_chunks"]["streams"]
    return {stream_id: [(x["boot_id"], x["seq"]) for x in lost] for stream_id, lost in streams.items()}


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
def test_the_lost_chunks_of_the_truth_are_the_seq_gaps_the_extractor_sees(seed: int):
    gravity = TRUTH_GRAVITY
    sim = _simulate(seed=seed, gravity=gravity, streams=GAP_STREAMS, packet_loss=PACKET_LOSS)
    lost = _lost_by_stream(sim)
    chunks = FeaturesRequest.model_validate(sim.payload).chunks

    ordered = order_chunks(chunks)

    assert lost["imu_t9"] and lost[STRETCH]
    for stream_id, truth_lost in lost.items():
        received = {(c["boot_id"], c["seq"]) for c in _chunks_of(sim.payload, stream_id)}
        assert not received & set(truth_lost)  # lo perdido no llega
        seqs = sorted(seq for _, seq in received)
        interior = {seq for _, seq in truth_lost if seqs[0] < seq < seqs[-1]}  # un hueco antes del primero recibido no cuenta
        gaps = [i for i in ordered.incidents if i.kind is IncidentKind.SEQ_GAP and i.stream_id == stream_id]
        assert sum(i.chunks for i in gaps) == len(interior)
        assert {seq for i in gaps for seq in range(i.seq, i.seq + i.chunks)} == interior


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
def test_with_seq_gaps_the_features_are_computed_and_flagged_with_the_coverage_as_score(seed: int):
    gravity = TRUTH_GRAVITY
    clean = _SIM._by_id(_extract(_simulate(seed=seed, gravity=gravity, streams=GAP_STREAMS, packet_loss=0.0).payload))
    sim = _simulate(seed=seed, gravity=gravity, streams=GAP_STREAMS, packet_loss=PACKET_LOSS)

    result = _extract(sim.payload)

    features = _SIM._by_id(result)
    assert _SIM._skipped(result) == {}
    assert set(features) == set(clean)  # nada se omite ni se inventa por los huecos
    assert not [i for i in clean if "packet_gap" in _quality_of(clean, i)["flags"]]
    coverage: dict[str, float] = {}
    for stream_id in ("imu_t9", STRETCH):
        seqs = sorted(c["seq"] for c in _chunks_of(sim.payload, stream_id))
        coverage[stream_id] = len(seqs) / (seqs[-1] - seqs[0] + 1)
        assert coverage[stream_id] < 1.0
    # lo que usa un solo stream lleva la cobertura de ese stream
    assert _quality_of(features, STRETCH_PEAK) == {
        "score": pytest.approx(coverage[STRETCH], abs=COVERAGE_ABS),
        "flags": ["packet_gap"],
        "assessed_by": "engine",
    }
    assert _quality_of(features, IMU_ROM) == {"score": pytest.approx(coverage["imu_t9"], abs=COVERAGE_ABS), "flags": ["packet_gap"], "assessed_by": "engine"}
    # las repeticiones se siguen contando por los eventos de la sesión
    assert features["exercise.rep_count"]["value"] == sim.truth["exercise.rep_count"]
    assert features[IMU_ROM]["value"] == pytest.approx(sim.truth[IMU_ROM], rel=IMU_ROM_LOSS_REL)
    assert features[STRETCH_PEAK]["value"] == pytest.approx(sim.truth[STRETCH_PEAK], rel=PER_REP_REL)
