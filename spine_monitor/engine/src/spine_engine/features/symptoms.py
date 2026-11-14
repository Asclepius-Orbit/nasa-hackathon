"""Features de síntomas (#47): agregados de 7 días de los autoinformes; función pura, sin reloj de pared.

- Ventana ``[as_of - 7 días, as_of]`` con ambos extremos incluidos (se compara el instante ``timestamp``).
- ``pain_nrs_7d_mean``/``stiffness_nrs_7d_mean`` salen de los informes ``daily``;
  ``sleep_hours_7d_mean`` solo de los ``daily`` que traen ``sleep.hours``. Los ``weekly_odi`` no aportan nada.
- ``red_flag_count_7d`` suma las banderas de los ``daily`` y de los ``red_flag``; una lista vacía cuenta 0,
  mientras que un campo ausente o nulo no aporta una observación.
- Una feature sin datos aplicables va a ``skipped`` con ``no_data``: nunca se rellena con ceros.
- Solo cuentan los informes y contextos del astronauta pedido.
- ``gravity_environment`` es el del contexto diario más reciente (por ``date``) dentro de la ventana, comparando
  fechas UTC (``start.date() <= date <= end.date()``); sin contexto, ``None``.
- ``computed_at`` es ``as_of``: no hay reloj en la lógica.
"""

import math
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from spine_contracts import (
    SCHEMA_VERSION,
    DailyContext,
    DailySelfReport,
    FeatureSet,
    FeatureValue,
    Quality,
    RedFlagReport,
    SelfReport,
)
from spine_contracts.catalog import unit_for
from spine_contracts.models.common_schema import AssessedBy
from spine_contracts.models.domain.feature_set_schema import Reason, SkippedItem, Window

# Ventana móvil de los agregados de síntomas: ``[as_of - 7 días, as_of]``, ambos extremos incluidos.
WINDOW = timedelta(days=7)

PAIN_MEAN = "symptoms.pain_nrs_7d_mean"
STIFFNESS_MEAN = "symptoms.stiffness_nrs_7d_mean"
SLEEP_MEAN = "symptoms.sleep_hours_7d_mean"
RED_FLAG_COUNT = "symptoms.red_flag_count_7d"

# Autoinforme: no hay muestreo que degrade, así que la calidad es máxima y sin flags (la pone el motor).
_QUALITY_SCORE = 1.0


def _mean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values)


def _feature(feature_id: str, value: float, n: int) -> FeatureValue:
    quality = Quality(score=_QUALITY_SCORE, flags=[], assessed_by=AssessedBy.ENGINE)
    return FeatureValue(feature_id=feature_id, value=value, unit=unit_for(feature_id), n=n, quality=quality)


def _latest_gravity(daily_context: Sequence[DailyContext], astronaut_id: str, first: datetime, last: datetime) -> DailyContext | None:
    first_day, last_day = first.astimezone(UTC).date(), last.astimezone(UTC).date()
    candidates = [c for c in daily_context if c.astronaut_id == astronaut_id and first_day <= c.date <= last_day]
    # desempate por valor para que el resultado no dependa del orden de entrada
    return max(candidates, key=lambda c: (c.date, c.gravity_environment.value), default=None)


def symptom_feature_set(self_reports: Sequence[SelfReport], daily_context: Sequence[DailyContext], as_of: datetime, astronaut_id: str) -> FeatureSet:
    """FeatureSet de ventana móvil (``session_id=None``) con los agregados de síntomas de ``astronaut_id``."""
    start = as_of - WINDOW
    reports = [r for r in self_reports if r.astronaut_id == astronaut_id and start <= r.timestamp <= as_of]
    dailies = [r for r in reports if isinstance(r, DailySelfReport)]
    flag_reports = [r for r in reports if isinstance(r, RedFlagReport)]
    sleeping = [r.sleep.hours for r in dailies if r.sleep is not None]
    flag_counts = [len(r.red_flags) for r in dailies if r.red_flags is not None] + [len(r.red_flags) for r in flag_reports]

    computed: list[FeatureValue] = []
    if dailies:
        pain = [r.pain_nrs for r in dailies]
        computed.append(_feature(PAIN_MEAN, _mean(pain), len(pain)))
        computed.append(_feature(STIFFNESS_MEAN, _mean([r.stiffness_nrs for r in dailies]), len(dailies)))
    if sleeping:
        computed.append(_feature(SLEEP_MEAN, _mean(sleeping), len(sleeping)))
    if flag_counts:
        computed.append(_feature(RED_FLAG_COUNT, float(sum(flag_counts)), len(flag_counts)))

    done = {f.feature_id for f in computed}
    skipped = [
        SkippedItem(feature_id=feature_id, reason=Reason.NO_DATA)
        for feature_id in sorted({PAIN_MEAN, STIFFNESS_MEAN, SLEEP_MEAN, RED_FLAG_COUNT} - done)
    ]
    latest = _latest_gravity(daily_context, astronaut_id, start, as_of)
    return FeatureSet(
        schema_version=SCHEMA_VERSION,
        astronaut_id=astronaut_id,
        session_id=None,
        protocol_id=None,
        gravity_environment=None if latest is None else latest.gravity_environment,
        computed_at=as_of,
        window=Window(start=start, end=as_of),
        features=sorted(computed, key=lambda f: f.feature_id),
        skipped=skipped,
    )
