"""Houston and outbox views (`app/ui-houston`, `app/ui-outbox`): what goes to the ground station and how its delivery is going."""

from collections.abc import Mapping, Sequence
from typing import Any

from spine_contracts import SCHEMA_VERSION

from monitor.db import GroundOutboxItem


def build_houston(astronaut_id: str, summary: Mapping[str, Any] | None, ground_link: Mapping[str, Any] | None, last_contact_at: str | None) -> dict[str, Any]:
    """The transport is simulated and the content pending validation by Aguado: both are constants of the contract today."""
    return {
        "schema_version": SCHEMA_VERSION,
        "astronaut_id": astronaut_id,
        "summary": summary,
        "ground_link": ground_link,
        "last_contact_at": last_contact_at,
        "transport": "simulated",
        "content_validated": False,
    }


def build_outbox(astronaut_id: str, items: Sequence[GroundOutboxItem], ground_link: Mapping[str, Any] | None, last_contact_at: str | None) -> dict[str, Any]:
    """Identity and delivery state of each entry, in the order given. Its payload and its last error stay out: no clinical content here."""
    return {
        "schema_version": SCHEMA_VERSION,
        "astronaut_id": astronaut_id,
        "ground_link": ground_link,
        "last_contact_at": last_contact_at,
        "items": [
            {
                "date": item.key.day.isoformat(),
                "mission_day": item.mission_day,
                "revision": item.key.revision,
                "state": item.state,
                "enqueued_at": item.enqueued_at,
                "received_at": item.received_at,
                "attempts": item.attempts,
            }
            for item in items
        ],
    }
