import random

import pytest
from spine_engine.deviation.detectors import AllAgreeVote, CompositeDetector, CusumDetector, KalmanDetector
from spine_engine.interfaces import ChangeSignal

# Series of z-scores, oldest first. Synthetic and seeded so the tests are deterministic.
NOISE = [random.Random(7).gauss(0.0, 1.0) for _ in range(30)]
DRIFT = [0.35 * i + e for i, e in enumerate(NOISE[:12])]
STEP_UP = NOISE[:6] + [2.5 + e * 0.3 for e in NOISE[6:12]]
STEP_DOWN = [-z for z in STEP_UP]
SPIKE = [0.1, -0.2, 0.0, 0.3, 9.0, 0.1, -0.1]

DETECTORS = [CusumDetector(), KalmanDetector(), CompositeDetector([CusumDetector(), KalmanDetector()], AllAgreeVote())]
IDS = ["cusum", "kalman", "both"]


@pytest.mark.parametrize("detector", DETECTORS, ids=IDS)
def test_noise_is_not_a_change(detector):
    assert not detector.detect(NOISE).sustained


@pytest.mark.parametrize("detector", DETECTORS, ids=IDS)
def test_a_single_spike_is_not_a_change(detector):
    assert not detector.detect(SPIKE).sustained


@pytest.mark.parametrize("detector", DETECTORS, ids=IDS)
def test_drift_is_detected(detector):
    assert detector.detect(DRIFT) == ChangeSignal(sustained=True, direction="up")


@pytest.mark.parametrize("detector", DETECTORS, ids=IDS)
def test_step_is_detected_with_its_direction(detector):
    assert detector.detect(STEP_UP) == ChangeSignal(sustained=True, direction="up")
    assert detector.detect(STEP_DOWN) == ChangeSignal(sustained=True, direction="down")


@pytest.mark.parametrize("detector", DETECTORS, ids=IDS)
def test_too_few_points_never_declare_a_change(detector):
    assert not detector.detect([3.0, 3.0]).sustained
    assert not detector.detect([]).sustained


def test_the_vote_needs_every_detector_to_agree():
    up, down, quiet = ChangeSignal(True, "up"), ChangeSignal(True, "down"), ChangeSignal()
    vote = AllAgreeVote()

    assert vote.decide([up, up]) == up
    assert vote.decide([up, quiet]) == quiet
    assert vote.decide([up, down]) == quiet
    assert vote.decide([]) == quiet


def test_composite_uses_the_injected_vote_policy():
    class AnyVote:
        def decide(self, signals):
            return next((s for s in signals if s.sustained), ChangeSignal())

    only_cusum_fires = CompositeDetector([CusumDetector(h=1.0, min_points=1), KalmanDetector(threshold=99.0)], AnyVote())

    assert only_cusum_fires.detect([3.0, 3.0, 3.0]).sustained
