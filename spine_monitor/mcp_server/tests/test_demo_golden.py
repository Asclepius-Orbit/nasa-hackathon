"""El dataset de demo con reloj fijo reproduce un snapshot versionado (estado completo legible + recuentos y hash del resto)."""

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from spine_contracts import SimulateRequest, dump
from spine_mcp_server.demo_data import build_demo_app_reader, build_demo_assess_base, build_demo_simulator

pytestmark = pytest.mark.golden

GOLDEN = Path(__file__).parent / "golden" / "demo-dataset.json"
AST = "AST-01"
# 12:34:56 debe dar lo mismo que 12:00:00 (redondeo a la hora); el segundo reloj fija la aritmética de fechas relativas.
NOWS = {
    "2026-11-13T12:00:00Z": datetime(2026, 11, 13, 12, 34, 56, tzinfo=UTC),
    "2026-11-06T09:00:00Z": datetime(2026, 11, 6, 9, 30, tzinfo=UTC),
}
STRETCH = "stretch.full_spine.peak_elongation_mm"
SIMULATIONS = {
    "no_overrides": [],
    "add_1mm": [{"feature_id": STRETCH, "mode": "add", "value": 1}],
    "add_minus_2mm": [{"feature_id": STRETCH, "mode": "add", "value": -2}],
    "set_7_1mm": [{"feature_id": STRETCH, "mode": "set", "value": 7.1}],
    "scale_half": [{"feature_id": STRETCH, "mode": "scale", "value": 0.5}],
    "mixed_clamped_high": [
        {"feature_id": STRETCH, "mode": "add", "value": 3},
        {"feature_id": STRETCH, "mode": "scale", "value": 1.5},
        {"feature_id": "imu.t9.sagittal.rom_deg", "mode": "add", "value": 9},
    ],
}


def _rounded(value: Any, digits: int = 6) -> Any:
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, dict):
        return {k: _rounded(v, digits) for k, v in value.items()}
    if isinstance(value, list):
        return [_rounded(v, digits) for v in value]
    return value


def _sha(value: Any) -> str:
    canonical = json.dumps(_rounded(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _block(value: Any, count: int) -> dict[str, Any]:
    return {"count": count, "sha256": _sha(value)}


def _snapshot_for(now: datetime) -> dict[str, Any]:
    reader = build_demo_app_reader(now)
    start, end = now - timedelta(days=30), now
    status = dump(reader.get_status(AST))
    history = dump(reader.get_history(AST, start, end))
    sessions = dump(reader.get_sessions(AST, start, end))
    reports = dump(reader.get_self_reports(AST, start, end))
    explanations = {a["assessment_id"]: _sha(dump(reader.get_explanation(a["assessment_id"]))) for a in history["assessments"]}
    base = dump(build_demo_assess_base(now).get_base(AST))
    simulator = build_demo_simulator(now)
    simulations: dict[str, Any] = {}
    for name, overrides in SIMULATIONS.items():
        request = SimulateRequest.model_validate({"schema_version": "1.0.0", "base": base, "overrides": overrides})
        response = dump(simulator.simulate(request))
        simulations[name] = {"risk_score": response["scenario"]["risk_score"], "level": response["scenario"]["level"], "sha256": _sha(response)}
    return {
        "status": _rounded(status),
        "history": {
            "feature_sets": _block(history["feature_sets"], len(history["feature_sets"])),
            "assessments": _block(history["assessments"], len(history["assessments"])),
            "window": _sha({k: history[k] for k in ("from", "to", "limit", "truncated")}),
        },
        "sessions": _block(sessions["items"], len(sessions["items"])),
        "self_reports": _block(reports["items"], len(reports["items"])),
        "explanations": {"count": len(explanations), "by_assessment_id": explanations},
        "assess_base": _block(base, len(base["feature_sets"])),
        "freshness": _block(status["freshness"], len(status["freshness"])),
        "simulations": simulations,
    }


def snapshot() -> dict[str, Any]:
    return {iso: _snapshot_for(now) for iso, now in NOWS.items()}


def test_demo_dataset_matches_the_golden_snapshot():
    current = snapshot()
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    assert GOLDEN.exists(), "golden snapshot missing: run with UPDATE_GOLDEN=1 and review the diff"
    assert current == json.loads(GOLDEN.read_text(encoding="utf-8"))
