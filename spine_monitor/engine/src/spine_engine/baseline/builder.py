"""Construcción de la baseline personal a partir de features de sesiones de referencia."""

from collections import defaultdict
from datetime import UTC, datetime
from statistics import median, pstdev
from uuid import uuid4

from spine_contracts import SCHEMA_VERSION, Baseline, BaselineEntry, BaselineRequest

from spine_engine.errors import InsufficientDataError


class StatisticalBaselineBuilder:
    """Una entrada por feature + protocolo + entorno de gravedad.

    Media y desviación, más mediana y MAD (robustas ante sesiones atípicas). Una feature con una sola
    sesión también tiene entrada (desviación y MAD 0): `n` dice cuánto fiarse. Los feature sets sin
    entorno de gravedad no se pueden comparar y se ignoran.
    """

    def build(self, request: BaselineRequest) -> Baseline:
        groups: dict[tuple, list[float]] = defaultdict(list)
        sessions: set[str] = set()
        for feature_set in request.feature_sets:
            if feature_set.gravity_environment is None:
                continue
            for feature in feature_set.features:
                groups[(feature.feature_id, feature_set.protocol_id, feature_set.gravity_environment)].append(feature.value)
                if feature_set.session_id is not None:
                    sessions.add(feature_set.session_id)
        if not groups or not sessions:
            raise InsufficientDataError("no feature set with a gravity environment and a session to build a baseline from")

        entries = [
            self._entry(feature_id, protocol_id, gravity, values)
            for (feature_id, protocol_id, gravity), values in sorted(groups.items(), key=_sort_key)
        ]
        return Baseline(
            schema_version=SCHEMA_VERSION,
            baseline_id=str(uuid4()),
            astronaut_id=request.astronaut_id,
            version=(request.previous_baseline_version or 0) + 1,
            created_at=datetime.now(UTC),
            built_from_session_ids=sorted(sessions),
            entries=entries,
        )

    @staticmethod
    def _entry(feature_id, protocol_id, gravity, values: list[float]) -> BaselineEntry:
        mid = median(values)
        entry = BaselineEntry(  # pyright: ignore[reportCallIssue]  # generated model: `protocol_id` has a positional Field default
            feature_id=feature_id,
            gravity_environment=gravity,
            n=len(values),
            mean=sum(values) / len(values),
            std=pstdev(values),
            median=mid,
            mad=median(abs(v - mid) for v in values),
            min=min(values),
            max=max(values),
        )
        # `dump` emite lo que se fijó: un `protocol_id=None` explícito saldría como null y el schema pide un string.
        if protocol_id is not None:
            entry.protocol_id = protocol_id
        return entry


def _sort_key(item: tuple) -> tuple:
    (feature_id, protocol_id, gravity), _ = item
    return feature_id, protocol_id or "", gravity.value
