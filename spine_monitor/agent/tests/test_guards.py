import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import spine_contracts
from spine_agent.guards import DISCLAIMER, fallback_reply, find_diagnostic_terms

EXAMPLES_DIR = Path(spine_contracts.__file__).resolve().parents[2] / "examples" / "valid" / "app"


def _example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def status() -> dict[str, Any]:
    return _example("status.json")


@pytest.fixture
def explanation() -> dict[str, Any]:
    return _example("explanation.json")


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        "I can't diagnose; please consult the flight surgeon.",
        "No puedo dar un diagnóstico; consulta al cirujano de vuelo.",
        "No puc fer un diagnòstic; consulta el cirurgià de vol.",
        "The level is amber: the lumbar load and your posture drive the risk score.",
        "Your spine data shows a peak elongation of 9.1 mm; read the disclaimer and discipline notes.",
        "A fractional change in the stretch sensor; the amber risk is rising.",
        "El nivel es ámbar; la carga lumbar y la postura explican el riesgo.",
        "",
    ],
)
def test_clean_answers_pass(text: str):
    assert find_diagnostic_terms(text) == []


REAL_REFUSALS = [
    "I don't know if you have a herniated disc. I cannot diagnose medical conditions, including herniated discs. "
    "Please refer to the flight surgeon for any clinical questions or concerns.",
    "I don't know. I cannot diagnose medical conditions, including whether symptoms are caused by sciatica. "
    "Please refer to the flight surgeon for clinical evaluation.",
]


@pytest.mark.unit
@pytest.mark.parametrize(
    "text",
    [
        *REAL_REFUSALS,
        "No fracture detected in the data.",
        "Sciatica is a nerve condition; ask the flight surgeon.",
        "Es una ciática clara.",
        "Hi ha una fractura.",
        "I can't tell whether you have a herniated disc.",
        "If you have a herniated disc, see the flight surgeon.",
        "I can't say you have sciatica, and I don't know if you were diagnosed with it.",
        "No puedo saber si tienes hernia discal.",
        "Si tienes una ciática, consulta al médico de vuelo.",
        "No puc saber si tens una ciàtica.",
        "Si tens una fractura, consulta el metge de vol.",
        "There is no sign that you have scoliosis.",
        "No tienes escoliosis según los datos.",
    ],
)
def test_refusals_and_bare_condition_names_are_clean(text: str):
    assert find_diagnostic_terms(text) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("You have a herniated disc.", ["you have a herniated disc"]),
        ("You probably have sciatica.", ["you probably have sciatica"]),
        ("You likely have an osteoporosis.", ["you likely have an osteoporosis"]),
        ("This looks like disc herniation.", ["looks like disc herniation"]),
        ("It seems to be a fracture.", ["it seems to be a fracture"]),
        ("That sounds like sciatica.", ["sounds like sciatica"]),
        ("This is spinal stenosis.", ["this is spinal stenosis"]),
        ("Tienes hernia discal.", ["tienes hernia discal"]),
        ("Probablemente tiene una escoliosis.", ["probablemente tiene una escoliosis"]),
        ("Parece una ciática.", ["parece una ciática"]),
        ("Esto es una fractura.", ["esto es una fractura"]),
        ("Usted padece escoliosis.", ["usted padece"]),
        ("Tens una ciàtica.", ["tens una ciàtica"]),
        ("Sembla una hernia de disc.", ["sembla una hernia de disc"]),
        ("Això és una fractura.", ["això és una fractura"]),
        ("You are suffering from back trouble.", ["you are suffering from"]),
        ("You suffer from back trouble.", ["you suffer from"]),
        ("You were diagnosed with it.", ["diagnosed with"]),
        ("He has been diagnosed already.", ["has been diagnosed"]),
        ("Padeces algo grave.", ["padeces"]),
        ("Pateixes algo greu.", ["pateixes"]),
        ("Not good: you have scoliosis.", ["you have scoliosis"]),
        ("I don't know. You have scoliosis, you have sciatica.", ["you have scoliosis", "you have sciatica"]),
        ("This is sciatica. This is sciatica.", ["this is sciatica"]),
    ],
)
def test_affirmative_claims_are_flagged(text: str, expected: list[str]):
    assert find_diagnostic_terms(text) == expected


@pytest.mark.unit
def test_terms_are_lowercase_and_deduplicated():
    assert find_diagnostic_terms("You have SCIATICA. You have Sciatica.") == ["you have sciatica"]


@pytest.mark.unit
def test_drivers_vocabulary_of_the_real_example_is_never_flagged(status: dict[str, Any], explanation: dict[str, Any]):
    assessment = status["latest_assessment"]
    texts = [d["feature_id"] for d in assessment["drivers"]]
    texts += [r["description"] for r in assessment["fired_rules"]]
    texts += [r["rule_id"] for r in explanation["assessment"]["fired_rules"]]
    assert all(find_diagnostic_terms(t) == [] for t in texts)


@pytest.mark.unit
def test_fallback_without_status_says_no_data_and_has_no_numbers():
    reply = fallback_reply(None, None)
    assert "no data" in reply.lower()
    assert not any(ch.isdigit() for ch in reply)
    assert DISCLAIMER in reply


@pytest.mark.unit
def test_fallback_without_status_ignores_explanation(explanation: dict[str, Any]):
    assert fallback_reply(None, explanation) == fallback_reply(None, None)


@pytest.mark.unit
def test_fallback_with_amber_status_quotes_level_and_drivers(status: dict[str, Any]):
    reply = fallback_reply(status, None)
    assert "AST-01" in reply
    assert "amber" in reply.lower()
    assert "stretch.lumbar.peak_elongation_mm: 9.1 mm" in reply
    assert "symptoms.pain_nrs_7d_mean: 5.1 nrs" in reply
    assert "RULE_SYMPTOM_PAIN_TREND" not in reply
    assert DISCLAIMER in reply


@pytest.mark.unit
def test_fallback_with_explanation_adds_rules_and_basis(status: dict[str, Any], explanation: dict[str, Any]):
    reply = fallback_reply(status, explanation)
    assert "RULE_SYMPTOM_PAIN_TREND" in reply
    assert "baseline" in reply.lower()
    assert "b45e11e0-0000-4000-8000-0000000000b1" in reply
    assert "engine 0.1.0" in reply
    assert reply.rstrip().endswith(DISCLAIMER)


@pytest.mark.unit
def test_fallback_reports_null_driver_value_as_not_available(status: dict[str, Any]):
    broken = deepcopy(status)
    broken["latest_assessment"]["drivers"][0]["value"] = None
    reply = fallback_reply(broken, None)
    assert "stretch.lumbar.peak_elongation_mm: not available" in reply
    assert "stretch.lumbar.peak_elongation_mm: 0" not in reply


@pytest.mark.unit
def test_fallback_with_missing_unit_omits_it(status: dict[str, Any]):
    broken = deepcopy(status)
    del broken["latest_assessment"]["drivers"][0]["unit"]
    assert "stretch.lumbar.peak_elongation_mm: 9.1\n" in fallback_reply(broken, None) + "\n"


@pytest.mark.unit
def test_fallback_with_empty_drivers_says_none_reported(status: dict[str, Any]):
    bare = deepcopy(status)
    bare["latest_assessment"]["drivers"] = []
    reply = fallback_reply(bare, None)
    assert "no main drivers" in reply.lower()
    assert DISCLAIMER in reply


@pytest.mark.unit
@pytest.mark.parametrize("payload", [{}, {"latest_assessment": None}])
def test_fallback_without_assessment_says_not_available(payload: dict[str, Any]):
    reply = fallback_reply({"astronaut_id": "AST-02", **payload}, None)
    assert "AST-02" in reply
    assert "no assessment" in reply.lower()
    assert DISCLAIMER in reply


@pytest.mark.unit
def test_fallback_with_null_level_says_not_available(status: dict[str, Any]):
    broken = deepcopy(status)
    broken["latest_assessment"]["level"] = None
    assert "level not available" in fallback_reply(broken, None).lower()


@pytest.mark.unit
def test_fallback_never_trips_the_diagnosis_filter(status: dict[str, Any], explanation: dict[str, Any]):
    for reply in (fallback_reply(None, None), fallback_reply(status, None), fallback_reply(status, explanation)):
        assert find_diagnostic_terms(reply) == []


@pytest.mark.unit
def test_disclaimer_denies_diagnosis_and_points_to_flight_surgeon():
    assert "not a medical diagnosis" in DISCLAIMER
    assert "flight surgeon" in DISCLAIMER
