"""Building the personal baseline from the features of reference sessions."""

from collections import defaultdict
from datetime import UTC, datetime
from statistics import median, pstdev
from uuid import uuid4

from spine_contracts import SCHEMA_VERSION, Baseline, BaselineEntry, BaselineRequest

from spine_engine.errors import InsufficientDataError
from spine_engine.features.symptoms import symptom_feature_set


class StatisticalBaselineBuilder:
    """One entry per feature + protocol + gravity environment.

    Mean and deviation, plus median and MAD (robust to atypical sessions). A feature with a single
    session also gets an entry (deviation and MAD 0): `n` says how far to trust it. Feature sets without
    a gravity environment cannot be compared and are ignored.
    """

    def build(self, request: BaselineRequest) -> Baseline:
        if any(feature_set.astronaut_id != request.astronaut_id for feature_set in request.feature_sets):
            raise InsufficientDataError("feature sets must belong to the requested astronaut")
        groups: dict[tuple, list[float]] = defaultdict(list)
        sessions: set[str] = set()
        for feature_set in request.feature_sets:
            if feature_set.gravity_environment is None:
                continue
            for feature in feature_set.features:
                groups[(feature.feature_id, feature_set.protocol_id, feature_set.gravity_environment)].append(feature.value)
                if feature_set.session_id is not None:
                    sessions.add(feature_set.session_id)
            if feature_set.session_id is not None and request.self_reports:
                anchor = feature_set.window.end if feature_set.window is not None else feature_set.computed_at
                symptoms = symptom_feature_set(request.self_reports, [], anchor, request.astronaut_id)
                for feature in symptoms.features:
                    groups[(feature.feature_id, None, feature_set.gravity_environment)].append(feature.value)
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
        # `dump` emits what was set: an explicit `protocol_id=None` would come out as null and the schema asks for a string.
        if protocol_id is not None:
            entry.protocol_id = protocol_id
        return entry


def _sort_key(item: tuple) -> tuple:
    (feature_id, protocol_id, gravity), _ = item
    return feature_id, protocol_id or "", gravity.value
