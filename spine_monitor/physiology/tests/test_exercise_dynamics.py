"""The exercise model's assumptions are testable and reproducible, not clinical measurements."""

from pathlib import Path

import numpy as np
from spine_physiology.config import load_config
from spine_physiology.signals import rep_schedule
from spine_physiology.signals.emg import activation_profile, exercise_fatigue_profile
from spine_physiology.signals.imu import angle_profile


def test_exercise_fatigue_recovers_and_varies_with_seed():
    config = load_config(Path(__file__).parents[1] / "configs/example-mission.yaml")
    protocol = config.protocols["lat_pulldown_standing_v1"]
    reps = rep_schedule(protocol, rng=np.random.default_rng(42))
    assert reps == rep_schedule(protocol, rng=np.random.default_rng(42))
    assert reps != rep_schedule(protocol, rng=np.random.default_rng(43))
    assert len(reps) == 60
    assert reps[0].start_s > 0
    assert reps[14].fatigue > reps[0].fatigue
    assert reps[15].fatigue < reps[14].fatigue
    assert reps[14].capacity < reps[0].capacity
    assert reps[14].end_s - reps[14].start_s > reps[0].end_s - reps[0].start_s
    assert all(a.end_s <= b.start_s for a, b in zip(reps, reps[1:], strict=False))
    lighter = rep_schedule(protocol.model_copy(update={"effort_load": 0.5}), rng=np.random.default_rng(42))
    stronger = rep_schedule(protocol.model_copy(update={"fatigue_resistance": 240.0}), rng=np.random.default_rng(42))
    faster_recovery = rep_schedule(protocol.model_copy(update={"recovery_per_s": 0.05}), rng=np.random.default_rng(42))
    assert lighter[14].fatigue < reps[14].fatigue
    assert stronger[14].fatigue < reps[14].fatigue
    assert faster_recovery[15].fatigue < reps[15].fatigue


def test_motion_and_activation_share_phases_and_rest():
    config = load_config(Path(__file__).parents[1] / "configs/example-mission.yaml")
    protocol = config.protocols["lat_pulldown_standing_v1"]
    reps = rep_schedule(protocol, rng=np.random.default_rng(42))
    rate = 50
    count = int((reps[-1].end_s + 10) * rate)
    angle = angle_profile(reps, rom_deg=[30 * r.amplitude_factor for r in reps], n_samples=count, rate_hz=rate)
    activation = activation_profile(reps, peak_pct_mvc=[70 * (1 + 0.4 * r.fatigue) for r in reps], rest_pct_mvc=2, n_samples=count, rate_hz=rate)
    fatigue = exercise_fatigue_profile(reps, n_samples=count, rate_hz=rate, recovery_per_s=protocol.recovery_per_s)
    assert np.all(angle[: int(reps[0].start_s * rate)] == 0)
    assert np.all(angle[int((reps[-1].end_s + 1) * rate) :] == 0)
    assert activation[0] == activation[-1] == 2
    first, last = reps[0], reps[14]

    def mid(rep):
        return int((rep.start_s + rep.end_s) / 2 * rate)

    assert angle[mid(first)] > angle[mid(last)]
    assert activation[mid(last)] > activation[mid(first)]
    assert fatigue[int(reps[15].start_s * rate)] < fatigue[int(reps[14].end_s * rate) - 1]
