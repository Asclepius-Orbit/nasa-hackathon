import pytest
from spine_contracts import validation

pytestmark = pytest.mark.integration


@pytest.fixture
def prediction_input():
    return {"reference_length_mm": 500, "elapsed_days": 5, "assumed_daily_change_mm": 0.2, "sensor_uncertainty_ohm": 10}


def stored_measurement(harness, *, posture="straight", baseline_gravity="earth_1g"):
    request = harness.example("engine/assess-request")
    baseline, feature_set = request["baseline"], request["feature_sets"][0]
    session = harness.example("domain/session.exercise")
    session["session_id"] = feature_set["session_id"]
    protocol = f"spine_resistance_{posture}_v1"
    feature_id = f"stretch.full_spine.{posture}_resistance_ohm"
    session["protocol_id"] = feature_set["protocol_id"] = protocol
    feature_set["features"] = [{**feature_set["features"][0], "feature_id": feature_id, "unit": "ohm", "value": 4600}]
    baseline["entries"] = [{"feature_id": feature_id, "protocol_id": protocol, "gravity_environment": baseline_gravity,
                            "n": 1, "mean": 4500, "median": 4500, "std": 0, "mad": 0}]
    harness.store.put_baseline(baseline)
    harness.store.put_feature_set(feature_set, phase="inflight", anchor_at=session["started_at"])
    return session["session_id"], baseline["baseline_id"]


async def test_prediction_uses_stored_personal_posture_baseline_and_session(harness, prediction_input):
    session_id, baseline_id = stored_measurement(harness)
    response = await harness.http.post(f"/ui/crew/AST-01/sessions/{session_id}/stretch-prediction", json=prediction_input)
    assert response.status_code == 200, response.text
    assert validation.errors("engine/stretch-estimate-response", response.json()) == []
    assert response.json()["prediction"]["reference_length_mm"] == 501
    assert response.headers["x-baseline-id"] == baseline_id


async def test_prediction_supports_flexion_and_profile_heights(harness, prediction_input):
    session_id, _ = stored_measurement(harness, posture="max_flexion")
    harness.store.put_profile(harness.example("domain/astronaut-profile"))
    response = await harness.http.post(f"/ui/crew/AST-01/sessions/{session_id}/stretch-prediction", json=prediction_input)
    assert response.status_code == 200
    assert response.json()["prediction"]["baseline_posture"] == "max_flexion"
    assert response.json()["prediction"]["standing_height_mm"] == 1720


async def test_prediction_includes_exercise_phase_when_recorded(harness, prediction_input):
    session_id, _ = stored_measurement(harness)
    session = harness.example("domain/session.exercise")
    session["session_id"] = session_id
    session["phase"] = "post"
    harness.store.insert_session(session, "processed")
    response = await harness.http.post(f"/ui/crew/AST-01/sessions/{session_id}/stretch-prediction", json=prediction_input)
    assert response.status_code == 200
    assert response.json()["prediction"]["exercise_phase"] == "post"


async def test_prediction_does_not_use_a_space_baseline(harness, prediction_input):
    session_id, _ = stored_measurement(harness, baseline_gravity="microgravity")
    response = await harness.http.post(f"/ui/crew/AST-01/sessions/{session_id}/stretch-prediction", json=prediction_input)
    assert response.status_code == 422


async def test_prediction_rejects_another_unlocked_person(harness, prediction_input):
    session_id, _ = stored_measurement(harness)
    response = await harness.http.post(f"/ui/crew/AST-02/sessions/{session_id}/stretch-prediction", json=prediction_input)
    assert response.status_code == 403


async def test_prediction_rejects_feature_set_of_another_person(harness, prediction_input):
    session_id, _ = stored_measurement(harness)
    feature_set = harness.store.feature_set(session_id)
    feature_set["astronaut_id"] = "AST-02"
    harness.store.put_feature_set(feature_set, phase="inflight", anchor_at="2027-02-09T08:40:00Z")
    response = await harness.http.post(f"/ui/crew/AST-01/sessions/{session_id}/stretch-prediction", json=prediction_input)
    assert response.status_code == 404


async def test_prediction_rejects_missing_or_extra_assumptions(harness, prediction_input):
    session_id, _ = stored_measurement(harness)
    del prediction_input["elapsed_days"]
    response = await harness.http.post(f"/ui/crew/AST-01/sessions/{session_id}/stretch-prediction", json=prediction_input)
    assert response.status_code == 422


async def test_prediction_rejects_out_of_range_uncertainty(harness, prediction_input):
    session_id, _ = stored_measurement(harness)
    prediction_input["sensor_uncertainty_ohm"] = 10000
    response = await harness.http.post(f"/ui/crew/AST-01/sessions/{session_id}/stretch-prediction", json=prediction_input)
    assert response.status_code == 422


async def test_prediction_requires_stored_session_features(harness, prediction_input):
    response = await harness.http.post("/ui/crew/AST-01/sessions/3f2b8c1e-6d4a-4b7e-9a10-2c5d8e7f1a03/stretch-prediction", json=prediction_input)
    assert response.status_code == 404
