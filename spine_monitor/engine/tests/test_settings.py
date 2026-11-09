import pytest
from pydantic import ValidationError
from spine_contracts import AssessmentDomain
from spine_engine.settings import EngineSettings


def test_domains_left_out_of_the_weights_default_to_one():
    settings = EngineSettings(_env_file=None, risk_domain_weights={AssessmentDomain.MUSCLE: 2.0})

    assert settings.risk_domain_weights == {**dict.fromkeys(AssessmentDomain, 1.0), AssessmentDomain.MUSCLE: 2.0}


def test_partial_weights_from_the_environment_are_completed(monkeypatch):
    monkeypatch.setenv("SPINE_ENGINE_RISK_DOMAIN_WEIGHTS", '{"muscle": 2}')

    settings = EngineSettings(_env_file=None)

    assert settings.risk_domain_weights == {**dict.fromkeys(AssessmentDomain, 1.0), AssessmentDomain.MUSCLE: 2.0}


def test_a_key_that_is_not_a_domain_is_rejected():
    with pytest.raises(ValidationError, match="risk_domain_weights"):
        EngineSettings(_env_file=None, risk_domain_weights={"not_a_domain": 1.0})
