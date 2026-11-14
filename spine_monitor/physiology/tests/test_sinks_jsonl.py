import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from spine_physiology import MissionGenerator
from spine_physiology.envelope import Envelope, EnvelopeKind
from spine_physiology.sinks.jsonl import FILES, MANIFEST_FILE, JsonlSink, day_dir, write_jsonl

AT = datetime(2030, 1, 1, tzinfo=UTC)

_spec = importlib.util.spec_from_file_location("_tiny_mission", Path(__file__).with_name("test_generator.py"))
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
tiny_config = _module.tiny_config


@pytest.fixture(scope="module")
def mission():
    config = tiny_config()
    return config, list(MissionGenerator(config).iter_messages())


def snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def env(kind, day=12, topic=None, payload=None, retained=False, astronaut="AST-01") -> Envelope:
    return Envelope(kind=kind, topic=topic, payload=payload or {"k": 1}, at=AT, retained=retained, astronaut_id=astronaut, day=day)


def test_day_dir_zero_pads(tmp_path):
    assert day_dir(tmp_path, "AST-01", 12) == tmp_path / "AST-01" / "day-012"


def test_layout_manifest_and_line_shapes(tmp_path, mission):
    config, messages = mission
    assert write_jsonl(messages, tmp_path, config=config, seed=config.seed) == len(messages)
    manifest = json.loads((tmp_path / MANIFEST_FILE).read_text(encoding="utf-8"))
    assert manifest["seed"] == config.seed
    assert manifest["config"] == config.model_dump(mode="json")
    assert manifest["package_version"] and manifest["contracts_version"]
    for astronaut in {m.astronaut_id for m in messages}:
        assert (tmp_path / astronaut / "profile.json").is_file()
    seen = {(m.kind, m.astronaut_id, m.day) for m in messages if m.kind is not EnvelopeKind.PROFILE}
    for kind, astronaut, day in seen:
        assert (day_dir(tmp_path, astronaut, day) / FILES[kind]).is_file()
    first = next(m for m in messages if m.kind is EnvelopeKind.STREAM_CHUNK)
    path = day_dir(tmp_path, first.astronaut_id, first.day) / FILES[EnvelopeKind.STREAM_CHUNK]
    line = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert list(line) == ["topic", "retained", "at", "payload"]
    assert line["payload"] == first.payload and line["topic"] == first.topic
    calibration = next(m for m in messages if m.kind is EnvelopeKind.CALIBRATION)
    cal_path = day_dir(tmp_path, calibration.astronaut_id, calibration.day) / "calibrations.jsonl"
    cal_line = json.loads(cal_path.read_text(encoding="utf-8").splitlines()[0])
    assert list(cal_line) == ["topic", "retained", "at", "payload"] and cal_line["retained"] is True
    assert cal_line["topic"] == calibration.topic and cal_line["payload"] == calibration.payload
    truth = next(m for m in messages if m.kind is EnvelopeKind.GROUND_TRUTH)
    truth_path = day_dir(tmp_path, truth.astronaut_id, truth.day) / FILES[EnvelopeKind.GROUND_TRUTH]
    assert list(json.loads(truth_path.read_text(encoding="utf-8").splitlines()[0])) == ["at", "payload"]


def test_bytes_deterministic_across_runs(tmp_path, mission):
    config, messages = mission
    write_jsonl(messages, tmp_path / "a", config=config, seed=config.seed)
    write_jsonl(MissionGenerator(config).iter_messages(), tmp_path / "b", config=config, seed=config.seed)
    assert snapshot(tmp_path / "a") == snapshot(tmp_path / "b")


def test_handle_cap_keeps_all_lines(tmp_path, mission):
    config, messages = mission
    write_jsonl(messages, tmp_path / "wide", config=config, seed=config.seed)
    with JsonlSink(tmp_path / "tiny", config=config, seed=config.seed, max_open_files=2) as sink:
        for m in messages:
            sink.write(m)
            assert len(sink._open) <= 2
    assert snapshot(tmp_path / "tiny") == snapshot(tmp_path / "wide")


def test_close_idempotent_and_write_after_close_fails(tmp_path, mission):
    config, _ = mission
    sink = JsonlSink(tmp_path, config=config, seed=1)
    sink.write(env(EnvelopeKind.SESSION))
    sink.close()
    sink.close()
    with pytest.raises(ValueError):
        sink.write(env(EnvelopeKind.SESSION))
    assert len((day_dir(tmp_path, "AST-01", 12) / FILES[EnvelopeKind.SESSION]).read_text(encoding="utf-8").splitlines()) == 1


def test_manifest_complete_flag_and_nan_rejected(tmp_path, mission):
    config, _ = mission
    with JsonlSink(tmp_path / "ok", config=config, seed=1) as sink:
        sink.write(env(EnvelopeKind.SESSION))
    assert json.loads((tmp_path / "ok" / MANIFEST_FILE).read_text(encoding="utf-8"))["complete"] is True
    with pytest.raises(RuntimeError), JsonlSink(tmp_path / "bad", config=config, seed=1) as sink:
        sink.write(env(EnvelopeKind.SESSION))
        raise RuntimeError("boom")
    assert json.loads((tmp_path / "bad" / MANIFEST_FILE).read_text(encoding="utf-8"))["complete"] is False
    with JsonlSink(tmp_path / "nan", config=config, seed=1) as sink, pytest.raises(ValueError):
        sink.write(env(EnvelopeKind.SESSION, payload={"x": float("nan")}))


def test_manifest_version_falls_back_to_unknown(tmp_path, mission, monkeypatch):
    from importlib import metadata

    def missing(name):
        raise metadata.PackageNotFoundError(name)

    monkeypatch.setattr(metadata, "version", missing)
    config, _ = mission
    JsonlSink(tmp_path, config=config, seed=1).close()
    manifest = json.loads((tmp_path / MANIFEST_FILE).read_text(encoding="utf-8"))
    assert manifest["package_version"] == "unknown" and manifest["contracts_version"] == "unknown"


def test_max_open_files_validated(tmp_path, mission):
    with pytest.raises(ValueError):
        JsonlSink(tmp_path, config=mission[0], seed=1, max_open_files=0)


def test_line_is_compact_utf8_with_lf_and_keeps_non_ascii(tmp_path, mission):
    config, _ = mission
    with JsonlSink(tmp_path, config=config, seed=1) as sink:
        sink.write(env(EnvelopeKind.DEVICE_STATUS, topic="t/é", retained=True, payload={"nombre": "Gonzalo, el ñandú", "n": [1, 2]}))
    raw = (day_dir(tmp_path, "AST-01", 12) / FILES[EnvelopeKind.DEVICE_STATUS]).read_bytes()
    expected = '{"topic":"t/é","retained":true,"at":"2030-01-01T00:00:00+00:00","payload":{"nombre":"Gonzalo, el ñandú","n":[1,2]}}\n'
    assert raw == expected.encode("utf-8")


def test_profile_is_indented_utf8_json_and_the_last_one_wins(tmp_path, mission):
    config, _ = mission
    with JsonlSink(tmp_path, config=config, seed=1) as sink:
        sink.write(env(EnvelopeKind.PROFILE, day=None, payload={"name": "viejo"}))
        sink.write(env(EnvelopeKind.PROFILE, day=None, payload={"name": "Ángela", "age": 41}))
    raw = (tmp_path / "AST-01" / "profile.json").read_bytes()
    assert raw == b'{\n  "name": "\xc3\x81ngela",\n  "age": 41\n}\n'


def test_profile_rejects_non_finite_numbers(tmp_path, mission):
    config, _ = mission
    with JsonlSink(tmp_path, config=config, seed=1) as sink, pytest.raises(ValueError):
        sink.write(env(EnvelopeKind.PROFILE, day=None, payload={"x": float("inf")}))


def test_envelope_without_a_day_is_rejected_with_its_kind(tmp_path, mission):
    config, _ = mission
    with JsonlSink(tmp_path, config=config, seed=1) as sink, pytest.raises(ValueError, match=r"^session envelope without day$"):
        sink.write(env(EnvelopeKind.SESSION, day=None))


def test_error_messages_name_the_problem(tmp_path, mission):
    config, _ = mission
    with pytest.raises(ValueError, match=r"^max_open_files must be >= 1$"):
        JsonlSink(tmp_path, config=config, seed=1, max_open_files=0)
    sink = JsonlSink(tmp_path, config=config, seed=1)
    sink.close()
    with pytest.raises(ValueError, match=r"^JsonlSink is closed$"):
        sink.write(env(EnvelopeKind.SESSION))


# `sink._open` se consulta porque el tope de manejadores abiertos y el orden LRU no se ven en los bytes escritos.
def test_a_single_open_file_is_allowed_and_the_default_cap_is_64(tmp_path, mission):
    config, _ = mission
    with JsonlSink(tmp_path / "one", config=config, seed=1, max_open_files=1) as sink:
        for day in (1, 2, 1):
            sink.write(env(EnvelopeKind.SESSION, day=day))
            assert len(sink._open) == 1
    with JsonlSink(tmp_path / "default", config=config, seed=1) as sink:
        for day in range(70):
            sink.write(env(EnvelopeKind.SESSION, day=day))
        assert len(sink._open) == 64


# `sink._open`: el orden LRU de manejadores no es observable en la salida.
def test_the_least_recently_used_file_is_the_one_closed(tmp_path, mission):
    config, _ = mission
    paths = {day: day_dir(tmp_path, "AST-01", day) / FILES[EnvelopeKind.SESSION] for day in (1, 2, 3)}
    with JsonlSink(tmp_path, config=config, seed=1, max_open_files=2) as sink:
        sink.write(env(EnvelopeKind.SESSION, day=1))
        sink.write(env(EnvelopeKind.SESSION, day=2))
        sink.write(env(EnvelopeKind.SESSION, day=1))  # 1 es ahora la más reciente
        sink.write(env(EnvelopeKind.SESSION, day=3))
        assert set(sink._open) == {paths[1], paths[3]}


# `sink._open`: que se reutilice el manejador (no reabrir) no es observable en la salida.
def test_an_open_file_is_reused_not_reopened(tmp_path, mission):
    config, _ = mission
    path = day_dir(tmp_path, "AST-01", 12) / FILES[EnvelopeKind.SESSION]
    with JsonlSink(tmp_path, config=config, seed=1) as sink:
        sink.write(env(EnvelopeKind.SESSION))
        handle = sink._open[path]
        sink.write(env(EnvelopeKind.SESSION))
        assert sink._open[path] is handle


def test_the_first_opening_truncates_a_file_left_by_a_previous_run(tmp_path, mission):
    config, _ = mission
    path = day_dir(tmp_path, "AST-01", 12) / FILES[EnvelopeKind.SESSION]
    path.parent.mkdir(parents=True)
    path.write_text("basura de una ejecución anterior\n", encoding="utf-8")
    with JsonlSink(tmp_path, config=config, seed=1) as sink:
        sink.write(env(EnvelopeKind.SESSION))
        sink.write(env(EnvelopeKind.SESSION))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["payload"] == {"k": 1} for line in lines)


def test_two_kinds_of_the_same_day_share_the_directory(tmp_path, mission):
    config, _ = mission
    with JsonlSink(tmp_path, config=config, seed=1) as sink:
        sink.write(env(EnvelopeKind.SESSION))
        sink.write(env(EnvelopeKind.SELF_REPORT))
    assert sorted(p.name for p in day_dir(tmp_path, "AST-01", 12).iterdir()) == ["self_reports.jsonl", "sessions.jsonl"]


def test_manifest_is_indented_json_with_the_installed_versions(tmp_path, mission):
    from importlib import metadata

    config, _ = mission
    out = tmp_path / "nested" / "run"
    JsonlSink(out, config=config, seed=7).close(complete=False)
    text = (out / MANIFEST_FILE).read_bytes().decode("utf-8")
    manifest = json.loads(text)
    assert text == json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    assert manifest["package_version"] == metadata.version("spine-physiology")
    assert manifest["contracts_version"] == metadata.version("spine-contracts")
    assert manifest["complete"] is False
