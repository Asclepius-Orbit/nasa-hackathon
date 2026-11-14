"""La misión mini con semilla fija reproduce un snapshot versionado (recuentos por tipo + hash de los mensajes)."""

import hashlib
import json
import os
from collections import Counter
from pathlib import Path

import pytest
import yaml
from spine_physiology import MissionGenerator
from spine_physiology.config import MissionConfig

pytestmark = pytest.mark.golden

EXAMPLE = Path(__file__).resolve().parents[1] / "configs" / "example-mission.yaml"
GOLDEN = Path(__file__).parent / "golden" / "tiny-mission.json"
SEED = 20261102


def tiny_config() -> MissionConfig:
    raw = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    raw["start_mission_day"] = -1
    raw["segments"] = [
        {"days": 1, "phase": "preflight", "gravity_environment": "earth_1g"},
        {"days": 2, "phase": "inflight", "gravity_environment": "microgravity"},
        {"days": 1, "phase": "postflight", "gravity_environment": "earth_1g"},
    ]
    raw["device"].update(power_on_time_utc="09:00", power_off_time_utc="09:40")
    raw["protocols"]["lat_pulldown_standing_v1"].update(
        lead_in_s=2.0, planned_sets=2, reps_per_set=2, rep_duration_s=2.0, rest_between_reps_s=1.0, rest_between_sets_s=2.0, lead_out_s=6.0
    )
    raw["protocols"]["spine_length_rest_v1"]["duration_s"] = 5.0
    for s in raw["schedule"]["sessions"]:
        if s["name"] == "lat_pulldown":
            s.update(time_utc="09:20", start_jitter_s=20.0)
    raw["schedule"]["background"].update(start_time_utc="09:19", end_time_utc="09:22")
    raw["signals"]["chunk_ms"] = 500
    return MissionConfig.model_validate(raw)


def _rounded(value, digits=4):
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, dict):
        return {k: _rounded(v, digits) for k, v in value.items()}
    if isinstance(value, list):
        return [_rounded(v, digits) for v in value]
    return value


def snapshot() -> dict:
    digest = hashlib.sha256()
    counts: Counter[str] = Counter()
    total = 0
    for env in MissionGenerator(tiny_config(), seed=SEED).iter_messages():
        counts[env.kind.value] += 1
        total += 1
        line = json.dumps([env.kind.value, env.topic, env.at.isoformat(), env.retained, _rounded(env.payload)], sort_keys=True, separators=(",", ":"))
        digest.update(line.encode())
    return {"seed": SEED, "messages": total, "by_kind": dict(sorted(counts.items())), "sha256": digest.hexdigest()}


def test_tiny_mission_matches_the_golden_snapshot():
    current = snapshot()
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8", newline="\n")
    assert GOLDEN.exists(), "golden snapshot missing: run with UPDATE_GOLDEN=1 and review the diff"
    assert current == json.loads(GOLDEN.read_text(encoding="utf-8"))
