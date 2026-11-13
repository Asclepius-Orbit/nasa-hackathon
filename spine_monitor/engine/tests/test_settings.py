import inspect

import pytest
from pydantic import ValidationError
from spine_contracts import AssessmentDomain
from spine_engine.features.stretch import StretchFamily
from spine_engine.settings import DEFAULT_STRETCH_RECOVERY_FRACTION, EngineSettings


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

@pytest.mark.parametrize(('wait', 'fraction'), [(-1.0, 0.1), (float('inf'), 0.1), (1.0, -0.1), (1.0, 1.1), (1.0, float('nan'))])
def test_invalid_stretch_protocol_settings_are_rejected(wait, fraction):
    with pytest.raises(ValidationError):
        EngineSettings(_env_file=None, stretch_recovery_wait_s=wait, stretch_recovery_fraction=fraction)


def test_stretch_protocol_settings_are_configurable(monkeypatch):
    monkeypatch.setenv('SPINE_ENGINE_STRETCH_RECOVERY_WAIT_S', '30')
    monkeypatch.setenv('SPINE_ENGINE_STRETCH_RECOVERY_FRACTION', '0.25')
    settings = EngineSettings(_env_file=None)
    assert settings.stretch_recovery_wait_s == 30
    assert settings.stretch_recovery_fraction == 0.25


def test_provisional_recovery_defaults_are_pinned_by_name():
    settings = EngineSettings(_env_file=None)

    assert settings.stretch_recovery_wait_s == 120.0
    assert settings.stretch_recovery_fraction == 0.1
    assert DEFAULT_STRETCH_RECOVERY_FRACTION == 0.1


def test_stretch_family_without_a_fraction_uses_the_settings_default():
    default = inspect.signature(StretchFamily.__init__).parameters["recovery_fraction"].default

    assert default == EngineSettings(_env_file=None).stretch_recovery_fraction
