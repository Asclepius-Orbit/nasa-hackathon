"""Registros de ground truth: lo que de verdad pasó, para evaluar el motor contra el simulador.

Dueño: C1. No hay schema de contrato: cada registro es un dict JSON-ready con `type` (uno de
`TruthType`), `astronaut_id`, `day` y los datos propios. Debe implementar:
- `latent_record`: latentes del día y la actividad (dosis) que los produjo.
- `device_record`: arranques (boot_id, boot_at, deriva) y reinicios del plan del día.
- `quality_faults_record`: episodios inyectados de una sesión/stream (flag, inicio/fin en UTC, score).
- `lost_chunks_record`: seq perdidos de un stream (boot_id, seq).
- `expected_features_record`: features esperadas de una sesión con ids del catálogo
  (`contracts/catalog/feature-catalog.json`), p. ej. emg.{muscle}.{side}.rms_pct_mvc,
  emg.{muscle}.{side}.median_freq_hz, imu.{spine_level}.{plane}.rom_deg, stretch.{region}.elongation_mm.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

from spine_physiology.device import ChunkOutcome, DevicePlan
from spine_physiology.faults import FaultEpisode
from spine_physiology.sessions import SessionPlan
from spine_physiology.state import DailyActivity, DayInfo, LatentState

TruthType = Literal["latent_state", "device_events", "quality_faults", "lost_chunks", "expected_features"]


def latent_record(state: LatentState, day: DayInfo, activity: DailyActivity) -> dict[str, Any]:
    raise NotImplementedError


def device_record(plan: DevicePlan, day: DayInfo) -> dict[str, Any]:
    raise NotImplementedError


def quality_faults_record(
    plan: SessionPlan,
    episodes: Sequence[FaultEpisode],
    *,
    stream_start: datetime,
    rate_hz: float,
) -> dict[str, Any]:
    raise NotImplementedError


def lost_chunks_record(plan: SessionPlan, stream_id: str, outcomes: Sequence[ChunkOutcome]) -> dict[str, Any]:
    """Solo los `outcomes` perdidos (chunk is None)."""
    raise NotImplementedError


def expected_features_record(plan: SessionPlan, features: Mapping[str, float]) -> dict[str, Any]:
    raise NotImplementedError
