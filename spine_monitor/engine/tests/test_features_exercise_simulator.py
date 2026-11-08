"""Familia ``exercise`` (#41) de extremo a extremo: sesiones del simulador por ``CompositeFeatureExtractor(default_registry())``.

La verdad es la del propio simulador: ``expected_features`` (``exercise.rep_count``, ``exercise.rep_duration_s_mean``)
y el calendario constante de ``rep_duration_s`` (por eso el CV real es 0).
"""

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import spine_physiology
from spine_contracts import FeaturesRequest, dump, validation
from spine_contracts.catalog import unit_for
from spine_contracts.models.domain.session_schema import Event, EventType
from spine_engine.features.composite import CompositeFeatureExtractor
from spine_engine.features.registry import default_registry

# vía el paquete instalado, no vía parents[]: bajo mutmut los tests se copian a otro árbol sin physiology/
_MISSION = Path(spine_physiology.__file__).resolve().parents[2] / "configs" / "example-mission.yaml"
COUNT = "exercise.rep_count"
MEAN = "exercise.rep_duration_s_mean"
CV = "exercise.rep_duration_cv_pct"
ALL_IDS = (COUNT, MEAN, CV)
REP_DURATION_S = 4.0  # rep_duration_s del protocolo de example-mission.yaml

# Con eventos la duración es exacta (los eventos son la verdad del calendario): tolerancia de redondeo.
EVENTS_MEAN_ABS_TOLERANCE_S = 1e-6
EVENTS_CV_ABS_TOLERANCE_PCT = 1e-6
# Solo IMU: el umbral de borde recorta algo los extremos (velocidad casi nula); media dentro del 10 %.
IMU_MEAN_REL_TOLERANCE = 0.10
# CV solo con IMU: calendario constante, el error de borde cambia poco entre reps. Observado 0,45 % (semilla 7)
# y 0,75 % (semilla 20261113), igual en 1 g y microgravedad; umbral con margen > 2x.
IMU_CV_MAX_PCT = 2.0

SEEDS = [7, 20261113]
GRAVITIES = ["earth_1g", "microgravity"]


def _mission_config(planned_reps: int | None = None) -> Any:
    import yaml
    from spine_physiology.config import MissionConfig

    raw = yaml.safe_load(_MISSION.read_text(encoding="utf-8"))
    raw["start_mission_day"] = 1
    raw["segments"] = [{"days": 1, "phase": "inflight", "gravity_environment": "microgravity"}]
    raw["device"].update(power_on_time_utc="09:55", power_off_time_utc="10:40")
    raw["protocols"]["lat_pulldown_standing_v1"].update(streams=["imu_l3"], lead_out_s=10.0)
    if planned_reps is not None:
        raw["protocols"]["lat_pulldown_standing_v1"]["planned_reps"] = planned_reps
    raw["schedule"]["sessions"] = [{**s, "start_jitter_s": 60.0} for s in raw["schedule"]["sessions"] if s["name"] == "lat_pulldown"]
    raw["schedule"]["background"].update(enabled=False, start_time_utc="09:56", end_time_utc="09:57")
    return MissionConfig.model_validate(raw)


def _simulate(seed: int, gravity: str, *, with_events: bool, planned_reps: int | None = None) -> tuple[FeaturesRequest, dict[str, float]]:
    """Petición de features de una sesión simulada y las features esperadas (verdad) del simulador."""
    from spine_contracts.models.common_schema import GravityEnvironment
    from spine_physiology import MissionGenerator
    from spine_physiology.envelope import EnvelopeKind
    from spine_physiology.sessions import SessionRef

    config = _mission_config(planned_reps)
    generator = MissionGenerator(config, seed)
    ref = SessionRef(config.crew[0].astronaut_id, 0, "lat_pulldown")
    messages = list(generator.session_messages(ref, gravity_environment=GravityEnvironment(gravity)))
    syncs = [m.payload for m in generator.day_messages(0, ref.astronaut_id) if m.kind is EnvelopeKind.CLOCK_SYNC]
    session = next(m.payload for m in messages if m.kind is EnvelopeKind.SESSION)
    if not with_events:
        session = {**session, "events": []}
    request = FeaturesRequest.model_validate(
        {
            "schema_version": "1.0.0",
            "session": session,
            "chunks": [m.payload for m in messages if m.kind is EnvelopeKind.STREAM_CHUNK],
            "clock_syncs": syncs,
        }
    )
    expected = next(m.payload for m in messages if m.kind is EnvelopeKind.GROUND_TRUTH and m.payload["type"] == "expected_features")
    return request, {f["feature_id"]: f["value"] for f in expected["features"]}


def _extract(request: FeaturesRequest) -> dict[str, Any]:
    result = dump(CompositeFeatureExtractor(default_registry()).extract(request))
    assert validation.errors("domain/feature-set", result) == []
    return result


def _by_id(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {f["feature_id"]: f for f in result["features"]}


@pytest.mark.integration
@pytest.mark.parametrize("gravity", GRAVITIES)
@pytest.mark.parametrize("seed", SEEDS)
def test_with_session_events_the_features_match_the_simulator_truth(seed: int, gravity: str):
    request, truth = _simulate(seed, gravity, with_events=True)

    result = _extract(request)

    features = _by_id(result)
    assert {f for f in features if f.startswith("exercise.")} == set(ALL_IDS)
    assert [s for s in result.get("skipped", []) if s["feature_id"] in ALL_IDS] == []
    assert features[COUNT]["value"] == truth[COUNT]
    assert features[MEAN]["value"] == pytest.approx(truth[MEAN], abs=EVENTS_MEAN_ABS_TOLERANCE_S)
    assert features[MEAN]["value"] == pytest.approx(REP_DURATION_S, abs=EVENTS_MEAN_ABS_TOLERANCE_S)
    assert features[CV]["value"] == pytest.approx(0.0, abs=EVENTS_CV_ABS_TOLERANCE_PCT)
    for feature_id in ALL_IDS:
        assert features[feature_id]["unit"] == unit_for(feature_id)
        assert features[feature_id]["n"] == truth[COUNT]


@pytest.mark.integration
@pytest.mark.parametrize("gravity", GRAVITIES)
@pytest.mark.parametrize("seed", SEEDS)
def test_without_events_the_imu_fallback_matches_the_simulator_truth(seed: int, gravity: str):
    request, truth = _simulate(seed, gravity, with_events=False)
    assert request.session.events == []

    result = _extract(request)

    features = _by_id(result)
    assert {f for f in features if f.startswith("exercise.")} == set(ALL_IDS)
    assert features[COUNT]["value"] == truth[COUNT]
    assert features[MEAN]["value"] == pytest.approx(truth[MEAN], rel=IMU_MEAN_REL_TOLERANCE)
    assert 0.0 <= features[CV]["value"] < IMU_CV_MAX_PCT
    for feature_id in ALL_IDS:
        assert features[feature_id]["unit"] == unit_for(feature_id)


@pytest.mark.integration
@pytest.mark.parametrize("seed", SEEDS)
def test_known_unequal_durations_give_an_exact_cv(seed: int):
    # 3, 4 y 5 s: media 4, desviación muestral 1 -> CV = 25 % exacto
    request, _truth = _simulate(seed, "earth_1g", with_events=True)
    start = min(e.timestamp for e in request.session.events or [])
    events: list[Event] = []
    cursor = 0.0
    for index, duration in enumerate((3.0, 4.0, 5.0), start=1):
        for kind, offset in (("rep_start", cursor), ("rep_end", cursor + duration)):
            events.append(
                Event(event_type=EventType(kind), timestamp=start + timedelta(seconds=offset), device_ms=None, boot_id=None, rep_index=index, note=None)
            )
        cursor += duration + 1.0
    custom = request.model_copy(update={"session": request.session.model_copy(update={"events": events})})

    features = _by_id(_extract(custom))

    assert features[COUNT]["value"] == 3.0
    assert features[MEAN]["value"] == pytest.approx(4.0, abs=EVENTS_MEAN_ABS_TOLERANCE_S)
    assert features[CV]["value"] == pytest.approx(25.0, abs=EVENTS_CV_ABS_TOLERANCE_PCT)


@pytest.mark.integration
@pytest.mark.parametrize("with_events", [True, False])
def test_a_single_rep_session_skips_all_three_as_too_few_repetitions(with_events: bool):
    request, truth = _simulate(7, "earth_1g", with_events=with_events, planned_reps=1)
    assert truth[COUNT] == 1.0

    result = _extract(request)

    assert not [f for f in result["features"] if f["feature_id"] in ALL_IDS]
    skipped = {s["feature_id"]: s["reason"] for s in result["skipped"]}
    assert {i: skipped.get(i) for i in ALL_IDS} == dict.fromkeys(ALL_IDS, "too_few_repetitions")
