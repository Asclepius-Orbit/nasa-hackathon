import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from spine_physiology import MissionGenerator
from spine_physiology.envelope import Envelope, EnvelopeKind
from spine_physiology.sinks.jsonl import FILES, MANIFEST_FILE, JsonlSink, day_dir, write_jsonl

AT = datetime(2030, 1, 1, tzinfo=UTC)

import importlib.util
from pathlib import Path

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


def test_max_open_files_validated(tmp_path, mission):
    with pytest.raises(ValueError):
        JsonlSink(tmp_path, config=mission[0], seed=1, max_open_files=0)
