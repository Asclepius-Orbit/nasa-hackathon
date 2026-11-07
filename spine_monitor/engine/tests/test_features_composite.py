"""Extractor compuesto y registro por familia (#40): se prueban con familias falsas, sin métricas reales."""

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from spine_contracts import FeaturesRequest, QualityFlag, dump, validation
from spine_contracts.catalog import UnknownFeatureIdError, all_feature_ids, unit_for
from spine_contracts.models.domain.feature_set_schema import Reason
from spine_engine.features.calibrations import CalibrationStatus
from spine_engine.features.composite import CompositeFeatureExtractor
from spine_engine.features.context import ExtractionContext
from spine_engine.features.registry import (
    ComputedFeature,
    ExtractorRegistry,
    FamilyResult,
    SkippedFeature,
    default_registry,
)

_EXAMPLE = Path(__file__).resolve().parents[2] / "contracts" / "examples" / "valid" / "engine" / "features-request.json"

STRETCH_ID = "stretch.lumbar.elongation_mm"
EMG_ID = "emg.latissimus_dorsi.left.rms_mv"
IMU_ID = "imu.l3.sagittal.rom_deg"
STRETCH = "stretch_lumbar"
IMU = "imu_l3"
EMG = "emg_lat_left"
OTHER_SESSION = "3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a99"


def payload() -> dict:
    return json.loads(_EXAMPLE.read_text(encoding="utf-8"))


def make_request(data: dict | None = None) -> FeaturesRequest:
    return FeaturesRequest.model_validate(data if data is not None else payload())


@dataclass
class FakeFamily:
    """Familia de pruebas: ``produce`` decide qué devuelve a partir del contexto y lo guarda para inspeccionarlo."""

    family: str
    feature_ids: tuple[str, ...]
    produce: Callable[[ExtractionContext], FamilyResult]
    seen: ExtractionContext | None = None

    def extract(self, context: ExtractionContext) -> FamilyResult:
        self.seen = context
        return self.produce(context)


def emitting(feature_id: str, stream: str, *, value: float = 1.5, family: str = "fake") -> FakeFamily:
    return FakeFamily(family, (feature_id,), lambda _: FamilyResult(computed=(ComputedFeature(feature_id, value, (stream,)),)))


def composite(*families: FakeFamily) -> CompositeFeatureExtractor:
    registry = ExtractorRegistry()
    for family in families:
        registry.register(family)
    return CompositeFeatureExtractor(registry)


def extract_json(extractor: CompositeFeatureExtractor, request: FeaturesRequest) -> dict:
    result = dump(extractor.extract(request))
    assert validation.errors("domain/feature-set", result) == []
    return result


def test_the_default_registry_is_empty_and_yields_an_honest_empty_feature_set():
    assert list(default_registry()) == []
    request = make_request()

    result = extract_json(CompositeFeatureExtractor(default_registry()), request)

    assert result["features"] == []
    assert result.get("skipped", []) == []
    assert result["session_id"] == request.session.session_id
    assert result["astronaut_id"] == request.session.astronaut_id
    assert result["protocol_id"] == request.session.protocol_id
    assert result["gravity_environment"] == request.session.mission_context.gravity_environment.value
    assert result["engine_version"] == "0.1.0"


def test_every_id_declared_by_the_default_registry_is_in_the_catalog():
    catalog = set(all_feature_ids())

    for family in default_registry():
        assert set(family.feature_ids) <= catalog, family.family


def test_a_registered_family_runs_and_its_feature_gets_the_catalog_unit_and_engine_quality():
    result = extract_json(composite(emitting(STRETCH_ID, STRETCH)), make_request())

    (feature,) = result["features"]
    assert feature["feature_id"] == STRETCH_ID
    assert feature["value"] == 1.5
    assert feature["unit"] == unit_for(STRETCH_ID)
    assert feature["quality"]["assessed_by"] == "engine"
    assert feature["quality"]["score"] == 1.0
    assert feature["quality"]["flags"] == []


def test_the_window_spans_the_mapped_chunk_times():
    result = extract_json(composite(), make_request())

    # ancla: device_ms 180000 = 10:03:00.100Z; stretch empieza en 184000 (4 muestras a 20 Hz), imu e emg en 184200
    assert datetime.fromisoformat(result["window"]["start"]) == datetime(2026, 11, 13, 10, 3, 4, 100000, tzinfo=UTC)
    assert datetime.fromisoformat(result["window"]["end"]) == datetime(2026, 11, 13, 10, 3, 4, 340000, tzinfo=UTC)  # imu: 4 muestras a 100 Hz


def test_the_window_is_omitted_without_clock_anchors():
    data = payload() | {"clock_syncs": []}

    result = extract_json(composite(), make_request(data))

    assert "window" not in result


def test_an_emitted_id_missing_from_the_catalog_is_rejected():
    with pytest.raises(UnknownFeatureIdError, match="not.in.the.catalog"):
        composite(emitting("not.in.the.catalog", STRETCH)).extract(make_request())


def test_a_skipped_id_missing_from_the_catalog_is_rejected():
    bad = FakeFamily("bad", (), lambda _: FamilyResult(skipped=(SkippedFeature("not.in.the.catalog", Reason.NO_DATA),)))

    with pytest.raises(UnknownFeatureIdError):
        composite(bad).extract(make_request())


def test_a_declared_stream_without_chunks_is_exposed_as_missing_and_nothing_is_invented():
    data = payload()
    data["chunks"] = [c for c in data["chunks"] if c["stream_id"] != IMU]

    def produce(context: ExtractionContext) -> FamilyResult:
        assert context.stream_missing(IMU)
        assert not context.stream_missing(STRETCH)
        assert context.segments(IMU) == ()
        return FamilyResult(skipped=(SkippedFeature(IMU_ID, Reason.STREAM_MISSING),))

    family = FakeFamily("imu", (IMU_ID,), produce)

    result = extract_json(composite(family), make_request(data))

    assert result["features"] == []
    assert result["skipped"] == [{"feature_id": IMU_ID, "reason": "stream_missing"}]


def test_chunks_of_undeclared_streams_or_other_sessions_are_ignored():
    data = payload()
    stray = dict(data["chunks"][0]) | {"stream_id": "emg_ghost"}
    foreign = dict(data["chunks"][0]) | {"session_id": OTHER_SESSION}
    data["chunks"] += [stray, foreign]
    family = emitting(EMG_ID, EMG)

    extract_json(composite(family), make_request(data))

    assert family.seen is not None
    assert [c.stream_id for c in family.seen.ordered.chunks()] == sorted([EMG, IMU, STRETCH])
    assert all(c.session_id == data["session"]["session_id"] for c in family.seen.ordered.chunks())


def test_a_request_without_any_usable_chunk_is_a_200_with_nothing_computed():
    data = payload()
    data["chunks"] = [dict(c) | {"session_id": OTHER_SESSION} for c in data["chunks"]]

    result = extract_json(composite(emitting(STRETCH_ID, STRETCH)), make_request(data))

    assert result["features"] == []
    assert result["skipped"] == [{"feature_id": STRETCH_ID, "reason": "no_data"}]


def test_a_feature_over_streams_without_utc_is_skipped_as_no_data():
    data = payload() | {"clock_syncs": []}

    result = extract_json(composite(emitting(STRETCH_ID, STRETCH)), make_request(data))

    assert result["features"] == []
    assert result["skipped"] == [{"feature_id": STRETCH_ID, "reason": "no_data"}]


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_a_non_finite_value_is_skipped_as_no_data(value):
    result = extract_json(composite(emitting(STRETCH_ID, STRETCH, value=value)), make_request())

    assert result["features"] == []
    assert result["skipped"] == [{"feature_id": STRETCH_ID, "reason": "no_data"}]


def test_a_missing_calibration_reaches_the_extractors_through_the_context():
    data = payload() | {"calibrations": []}
    seen: list = []

    def produce(context: ExtractionContext) -> FamilyResult:
        resolution = context.calibration(STRETCH)
        seen.append(resolution)
        return FamilyResult(skipped=(SkippedFeature(STRETCH_ID, resolution.reason or Reason.NO_DATA),))

    result = extract_json(composite(FakeFamily("s", (STRETCH_ID,), produce)), make_request(data))

    assert seen[0].status is CalibrationStatus.MISSING
    assert result["skipped"] == [{"feature_id": STRETCH_ID, "reason": "no_calibration"}]


def test_an_expired_calibration_reaches_the_extractors_and_flags_the_quality():
    data = payload()
    for calibration in data["calibrations"]:
        if calibration["stream_id"] == STRETCH:
            calibration["valid_until"] = "2026-11-13T10:00:00Z"
    resolutions: list = []

    def produce(context: ExtractionContext) -> FamilyResult:
        resolutions.append(context.calibration(STRETCH))
        return FamilyResult(
            computed=(ComputedFeature(STRETCH_ID, 2.0, (STRETCH,)),),
            skipped=(SkippedFeature(IMU_ID, context.calibration(IMU).reason or Reason.NO_DATA),) if context.calibration(IMU).reason else (),
        )

    result = extract_json(composite(FakeFamily("s", (STRETCH_ID,), produce)), make_request(data))

    assert resolutions[0].reason is Reason.CALIBRATION_EXPIRED
    assert result["features"][0]["quality"]["flags"] == [QualityFlag.OUT_OF_CALIBRATION.value]
    assert result["features"][0]["quality"]["score"] == 1.0


def test_quality_covers_only_the_streams_the_feature_used():
    data = payload()
    gap = dict(data["chunks"][2]) | {"seq": data["chunks"][2]["seq"] + 3}  # stretch: hueco de 2 chunks
    data["chunks"].append(gap)

    result = extract_json(composite(emitting(STRETCH_ID, STRETCH), emitting(EMG_ID, EMG)), make_request(data))

    by_id = {f["feature_id"]: f["quality"] for f in result["features"]}
    assert by_id[STRETCH_ID]["score"] < 1.0
    assert "packet_gap" in by_id[STRETCH_ID]["flags"]
    assert by_id[EMG_ID]["score"] == 1.0


def test_an_extractor_quality_override_replaces_the_common_one():
    from spine_contracts import Quality
    from spine_contracts.models.common_schema import AssessedBy

    custom = Quality(score=0.25, flags=[], assessed_by=AssessedBy.ENGINE)
    family = FakeFamily("q", (STRETCH_ID,), lambda _: FamilyResult(computed=(ComputedFeature(STRETCH_ID, 1.0, (STRETCH,), n=3, quality=custom),)))

    result = extract_json(composite(family), make_request())

    assert result["features"][0]["quality"]["score"] == 0.25
    assert result["features"][0]["n"] == 3


def test_clock_uncertainty_above_the_configured_threshold_flags_the_quality():
    registry = ExtractorRegistry()
    registry.register(emitting(STRETCH_ID, STRETCH))
    request = make_request()  # ancla con incertidumbre de 18 ms

    flagged = dump(CompositeFeatureExtractor(registry, clock_uncertain_ms=10).extract(request))
    unflagged = dump(CompositeFeatureExtractor(registry, clock_uncertain_ms=18).extract(request))
    default = dump(CompositeFeatureExtractor(registry).extract(request))

    assert flagged["features"][0]["quality"]["flags"] == ["clock_uncertain"]
    assert unflagged["features"][0]["quality"]["flags"] == []
    assert default["features"][0]["quality"]["flags"] == []


def test_output_is_deterministic_whatever_the_registration_order():
    a = emitting(STRETCH_ID, STRETCH, family="a")
    b = emitting(EMG_ID, EMG, family="b")
    skip = FakeFamily("c", (IMU_ID,), lambda _: FamilyResult(skipped=(SkippedFeature(IMU_ID, Reason.NO_DATA),)))
    request = make_request()

    forward = dump(composite(a, b, skip).extract(request))
    backward = dump(composite(skip, b, a).extract(request))

    forward.pop("computed_at"), backward.pop("computed_at")
    assert forward == backward
    ids = [f["feature_id"] for f in forward["features"]]
    assert ids == sorted(ids)


def test_the_registry_runs_families_in_registration_order():
    order: list[str] = []

    def tracking(name: str) -> FakeFamily:
        def produce(_: ExtractionContext) -> FamilyResult:
            order.append(name)
            return FamilyResult()

        return FakeFamily(name, (), produce)

    composite(tracking("z"), tracking("a"), tracking("m")).extract(make_request())

    assert order == ["z", "a", "m"]


def test_a_feature_emitted_twice_keeps_the_first_and_never_both_computed_and_skipped():
    first = FakeFamily("1", (STRETCH_ID,), lambda _: FamilyResult(computed=(ComputedFeature(STRETCH_ID, 1.0, (STRETCH,)),)))
    second = FakeFamily(
        "2",
        (STRETCH_ID,),
        lambda _: FamilyResult(
            computed=(ComputedFeature(STRETCH_ID, 9.0, (STRETCH,)),),
            skipped=(SkippedFeature(STRETCH_ID, Reason.NO_DATA), SkippedFeature(EMG_ID, Reason.NO_DATA), SkippedFeature(EMG_ID, Reason.NO_DATA)),
        ),
    )

    result = extract_json(composite(first, second), make_request())

    assert [(f["feature_id"], f["value"]) for f in result["features"]] == [(STRETCH_ID, 1.0)]
    assert result["skipped"] == [{"feature_id": EMG_ID, "reason": "no_data"}]


@st.composite
def requests(draw) -> FeaturesRequest:
    data = payload()
    chunks = [c for c in data["chunks"] if draw(st.booleans())]
    extra = []
    for chunk in list(chunks):
        for _ in range(draw(st.integers(0, 2))):
            extra.append(
                chunk
                | {
                    "seq": draw(st.integers(0, 2000)),
                    "boot_id": draw(st.sampled_from([chunk["boot_id"], 7])),
                    "session_id": draw(st.sampled_from([chunk["session_id"], OTHER_SESSION])),
                    "stream_id": draw(st.sampled_from([chunk["stream_id"], "emg_ghost"])),
                    "dropped_samples": draw(st.integers(0, 5)),
                }
            )
    chunks += extra
    if not chunks:
        chunks = [data["chunks"][0]]
    data["chunks"] = draw(st.permutations(chunks))
    if draw(st.booleans()):
        data["clock_syncs"] = []
    if draw(st.booleans()):
        data["calibrations"] = []
    for calibration in data["calibrations"]:
        if draw(st.booleans()):
            calibration["valid_until"] = draw(st.sampled_from(["2020-01-01T00:00:00Z", "2099-01-01T00:00:00Z"]))
    return make_request(data)


def all_streams_family() -> FakeFamily:
    def produce(context: ExtractionContext) -> FamilyResult:
        pairs = [(STRETCH_ID, STRETCH), (EMG_ID, EMG), (IMU_ID, IMU)]
        computed = tuple(ComputedFeature(fid, 1.0, (stream,)) for fid, stream in pairs if not context.stream_missing(stream))
        skipped = tuple(SkippedFeature(fid, Reason.STREAM_MISSING) for fid, stream in pairs if context.stream_missing(stream))
        return FamilyResult(computed=computed, skipped=skipped)

    return FakeFamily("all", (STRETCH_ID, EMG_ID, IMU_ID), produce)


@pytest.mark.property
@given(request=requests())
def test_the_output_always_validates_against_the_feature_set_schema(request):
    for extractor in (CompositeFeatureExtractor(default_registry()), composite(all_streams_family())):
        result = dump(extractor.extract(request))

        assert validation.errors("domain/feature-set", result) == []
        quality = [f["quality"] for f in result["features"]]
        assert all(q["assessed_by"] == "engine" and 0.0 < q["score"] <= 1.0 for q in quality)
        computed = {f["feature_id"] for f in result["features"]}
        skipped = {s["feature_id"] for s in result.get("skipped", [])}
        assert computed.isdisjoint(skipped)
