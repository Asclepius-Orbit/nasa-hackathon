"""Familia ``stretch`` (#45) de extremo a extremo: sesiones del simulador por ``CompositeFeatureExtractor(default_registry())``.

La verdad es ``expected_features`` del simulador: ``stretch.{region}.peak_elongation_mm`` y ``range_mm`` (longitud real del
cordón durante las repeticiones, sin ruido ni histéresis) y ``elongation_mm`` / ``elongation_pct`` / ``rest_resistance_ohm``
(media de la longitud real en un ``spine_check`` o en el fondo). La extracción parte de las cuentas ADC con ruido y de la
calibración medida (curva con ``measurement_noise_mm``), así que se compara con tolerancia.

La verdad NO trae ``recovery_time_s`` (el simulador no modela la recuperación como feature): ``recovery_time_s`` no se
compara con ninguna verdad y solo se comprueba que es finito y no negativo cuando sale.

Este fichero define además los ayudantes de sesión completos (todos los streams, fallos, pérdida de chunks...) que cargan
por ruta ``test_features_failures_simulator.py`` y ``test_baseline_simulator.py``: con ``--import-mode=importlib`` los
tests no se importan entre sí por nombre.
"""

import copy
import math
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, NamedTuple

import pytest
import spine_physiology
from spine_contracts import FeaturesRequest, dump, validation
from spine_contracts.catalog import unit_for
from spine_engine.features.composite import CompositeFeatureExtractor
from spine_engine.features.registry import default_registry

# vía el paquete instalado, no vía parents[]: bajo mutmut los tests se copian a otro árbol sin physiology/
_MISSION = Path(spine_physiology.__file__).resolve().parents[2] / "configs" / "example-mission.yaml"
REGION = "full_spine"
PREFIX = f"stretch.{REGION}"
STRETCH = "stretch_spine"
ALL_STREAMS = ("emg_lat_left", "emg_lat_right", "imu_t9", STRETCH)
SEEDS = [7, 20261113]
GRAVITIES = ["earth_1g", "microgravity"]
EXERCISE, SPINE_CHECK, BACKGROUND = "lat_pulldown", "pre_spine_check", "background"
REST_LENGTH_MM = 650.0  # baseline.cord_rest_length_mm del astronauta AST-01 en example-mission.yaml

# Tolerancias frente a la verdad; observado en las 4 combinaciones semilla x gravedad.
# Pico y rango por repetición: la verdad es la longitud real sin ruido ni histéresis, la extracción ve el ruido ADC (el
# máximo de ~60 muestras con ruido sesga el pico hacia arriba y el rango en ambos extremos) y la histéresis (solo el pico):
# observado +21..22 % el pico y +18..22 % el rango, siempre por encima de la verdad; umbral 30 %.
PER_REP_REL = 0.30
# Elongación pasiva de un spine_check: solo ruido ADC y de la curva medida sobre ~2400 muestras: observado hasta 0,03 mm;
# umbral 0,1 mm. En el fondo la extracción detecta las flexiones como repeticiones y excluye cada una más la espera de
# recuperación de las muestras pasivas (la verdad promedia todo lo emitido): con la semilla 20261113 quedan 305 de 1010
# muestras y el error sube a 0,22 mm; umbral 0,5 mm. Los % equivalen a esos mm sobre la longitud de reposo.
ELONGATION_MM_ABS = {SPINE_CHECK: 0.1, BACKGROUND: 0.5}
ELONGATION_PCT_ABS = {slot: 100.0 * mm / REST_LENGTH_MM for slot, mm in ELONGATION_MM_ABS.items()}
# Resistencia de reposo: se invierte el ADC con la resistencia serie exacta: observado < 0,04 % incluso en el fondo; umbral 0,1 %.
REST_RESISTANCE_REL = 1e-3
# n de las pasivas: la verdad cuenta las muestras emitidas y la extracción descarta las ADC saturadas o en 0, los chunks
# perdidos y (fondo) las flexiones; nunca puede superar a la verdad. Fracción mínima observada: spine_check 99,7 %, fondo 30 %.
PASSIVE_N_MIN_FRACTION = {SPINE_CHECK: 0.95, BACKGROUND: 0.25}


@dataclass(frozen=True, slots=True)
class Scenario:
    """Variaciones sobre la misión de ejemplo; hashable para cachear la simulación."""

    streams: tuple[str, ...] = (STRETCH,)
    faults: bool = False
    # probabilidad de perder un chunk: None deja la de la misión de ejemplo
    packet_loss: float | None = None
    imu_rate_hz: float | None = None
    # None deja los 100 ms de la misión; chunks más largos abaratan mucho la extracción (que recorre los chunks por repetición)
    chunk_ms: int | None = None
    days: int = 1
    phase: str = "inflight"
    gravity: str = "microgravity"


class Simulated(NamedTuple):
    """Petición de features de una sesión simulada y su verdad."""

    payload: dict[str, Any]
    truth: dict[str, float]
    truth_n: dict[str, int | None]
    records: dict[str, dict[str, Any]]


def _mission_config(scenario: Scenario) -> Any:
    import yaml
    from spine_physiology.config import MissionConfig

    raw = yaml.safe_load(_MISSION.read_text(encoding="utf-8"))
    raw["start_mission_day"] = 1
    raw["segments"] = [{"days": scenario.days, "phase": scenario.phase, "gravity_environment": scenario.gravity}]
    raw["device"].update(power_on_time_utc="09:55", power_off_time_utc="10:40")
    raw["protocols"]["lat_pulldown_standing_v1"].update(streams=list(scenario.streams), lead_out_s=10.0)
    if not scenario.faults:
        # los artefactos inyectados (clipping, saturación...) alteran la señal sin tocar la verdad: se comparan sin ellos
        for fault in raw["faults"]["quality"].values():
            fault["probability"] = 0.0
    if scenario.packet_loss is not None:
        raw["faults"]["device"]["packet_loss"]["probability"] = scenario.packet_loss
    if scenario.imu_rate_hz is not None:
        raw["signals"]["imu"]["sample_rate_hz"] = scenario.imu_rate_hz
    if scenario.chunk_ms is not None:
        raw["signals"]["chunk_ms"] = scenario.chunk_ms
    # las sesiones con `bracket` (spine_check) no admiten jitter propio
    raw["schedule"]["sessions"] = [s if "bracket" in s else {**s, "start_jitter_s": 60.0} for s in raw["schedule"]["sessions"]]
    raw["schedule"]["background"].update(start_time_utc="09:56", end_time_utc="09:58")
    return MissionConfig.model_validate(raw)


@cache
def _simulated(scenario: Scenario, seed: int, slot: str, day: int) -> Simulated:
    from spine_contracts.models.common_schema import GravityEnvironment
    from spine_physiology import MissionGenerator
    from spine_physiology.envelope import EnvelopeKind
    from spine_physiology.sessions import SessionRef

    config = _mission_config(scenario)
    generator = MissionGenerator(config, seed)
    ref = SessionRef(config.crew[0].astronaut_id, day, slot)
    messages = list(generator.session_messages(ref, gravity_environment=GravityEnvironment(scenario.gravity)))
    # el helper de sesión no trae las calibraciones del día ni los clock_sync: los emite el generador del día
    day_messages = list(generator.day_messages(day, ref.astronaut_id))
    payload = {
        "schema_version": "1.0.0",
        "session": next(m.payload for m in messages if m.kind is EnvelopeKind.SESSION),
        "chunks": [m.payload for m in messages if m.kind is EnvelopeKind.STREAM_CHUNK],
        "clock_syncs": [m.payload for m in day_messages if m.kind is EnvelopeKind.CLOCK_SYNC],
        "calibrations": [m.payload for m in [*messages, *day_messages] if m.kind is EnvelopeKind.CALIBRATION],
    }
    records = {m.payload["type"]: m.payload for m in messages if m.kind is EnvelopeKind.GROUND_TRUTH}
    features = records["expected_features"]["features"]
    return Simulated(payload, {f["feature_id"]: f["value"] for f in features}, {f["feature_id"]: f.get("n") for f in features}, records)


def _simulate(scenario: Scenario, seed: int, slot: str, day: int = 0) -> Simulated:
    """Copia propia (los tests la modifican) de :func:`_simulated`."""
    return copy.deepcopy(_simulated(scenario, seed, slot, day))


def _extract(payload: dict[str, Any]) -> dict[str, Any]:
    result = dump(CompositeFeatureExtractor(default_registry()).extract(FeaturesRequest.model_validate(payload)))
    assert validation.errors("domain/feature-set", result) == []
    return result


def _by_id(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["feature_id"]: f for f in result["features"]}


def _skipped(result: dict[str, Any]) -> dict[str, str]:
    return {s["feature_id"]: s["reason"] for s in result.get("skipped", [])}


def _stretch(name: str) -> str:
    return f"{PREFIX}.{name}"


PER_REP = ("peak_elongation_mm", "range_mm")
PASSIVE = ("elongation_mm", "elongation_pct", "rest_resistance_ohm")


@pytest.mark.integration
@pytest.mark.parametrize("gravity", GRAVITIES)
@pytest.mark.parametrize("seed", SEEDS)
def test_the_per_repetition_features_match_the_simulator_truth(seed: int, gravity: str):
    sim = _simulate(Scenario(gravity=gravity), seed, EXERCISE)

    result = _extract(sim.payload)

    features = _by_id(result)
    assert {f for f in features if f.startswith("stretch.")} >= {_stretch(n) for n in PER_REP}
    assert not [i for i in _skipped(result) if i.endswith(PER_REP)]
    for name in PER_REP:
        feature = features[_stretch(name)]
        assert feature["value"] == pytest.approx(sim.truth[_stretch(name)], rel=PER_REP_REL)
        assert feature["n"] == sim.truth_n[_stretch(name)] == sim.truth["exercise.rep_count"]
        assert feature["unit"] == unit_for(_stretch(name))
        assert feature["quality"]["assessed_by"] == "engine"
    assert features[_stretch("peak_elongation_mm")]["value"] > features[_stretch("range_mm")]["value"] > 0.0


@pytest.mark.integration
@pytest.mark.parametrize("gravity", GRAVITIES)
@pytest.mark.parametrize("seed", SEEDS)
def test_the_recovery_time_has_no_truth_and_is_only_checked_for_being_plausible(seed: int, gravity: str):
    # El simulador no emite recovery_time_s en expected_features: no hay valor con el que compararlo.
    sim = _simulate(Scenario(gravity=gravity), seed, EXERCISE)
    assert _stretch("recovery_time_s") not in sim.truth

    features = _by_id(_extract(sim.payload))

    recovery = features.get(_stretch("recovery_time_s"))
    if recovery is not None:
        assert math.isfinite(recovery["value"]) and recovery["value"] >= 0.0
        assert 0 < recovery["n"] <= sim.truth["exercise.rep_count"]


@pytest.mark.integration
@pytest.mark.parametrize("slot", [SPINE_CHECK, BACKGROUND])
@pytest.mark.parametrize("gravity", GRAVITIES)
@pytest.mark.parametrize("seed", SEEDS)
def test_the_passive_features_match_the_simulator_truth(seed: int, gravity: str, slot: str):
    sim = _simulate(Scenario(gravity=gravity), seed, slot)

    result = _extract(sim.payload)

    features = _by_id(result)
    assert {_stretch(n) for n in PASSIVE} <= set(features)
    # sin repeticiones no hay verdad ni valor por repetición: se omiten con motivo y no se inventan
    assert {i: r for i, r in _skipped(result).items() if i.endswith(PER_REP)} == dict.fromkeys((_stretch(n) for n in PER_REP), "too_few_repetitions")
    assert features[_stretch("elongation_mm")]["value"] == pytest.approx(sim.truth[_stretch("elongation_mm")], abs=ELONGATION_MM_ABS[slot])
    assert features[_stretch("elongation_pct")]["value"] == pytest.approx(sim.truth[_stretch("elongation_pct")], abs=ELONGATION_PCT_ABS[slot])
    assert features[_stretch("rest_resistance_ohm")]["value"] == pytest.approx(sim.truth[_stretch("rest_resistance_ohm")], rel=REST_RESISTANCE_REL)
    for name in PASSIVE:
        feature, expected_n = features[_stretch(name)], sim.truth_n[_stretch(name)]
        assert expected_n is not None
        assert PASSIVE_N_MIN_FRACTION[slot] * expected_n <= feature["n"] <= expected_n
        assert feature["unit"] == unit_for(_stretch(name))
        assert feature["quality"]["assessed_by"] == "engine"


@pytest.mark.integration
def test_the_exercise_session_publishes_no_passive_truth_and_the_spine_check_no_per_repetition_truth():
    # la verdad de cada tipo de sesión es disjunta: así se sabe que cada test compara con lo que corresponde
    exercise = _simulate(Scenario(), 7, EXERCISE).truth
    check = _simulate(Scenario(), 7, SPINE_CHECK).truth

    assert {i for i in exercise if i.startswith("stretch.")} == {_stretch(n) for n in PER_REP}
    assert {i for i in check if i.startswith("stretch.")} == {_stretch(n) for n in PASSIVE}
