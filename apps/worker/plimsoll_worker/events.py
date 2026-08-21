"""Announcing what happened to a run, for whatever subscribed to it.

Separate from the audit trail. The trail records what a person or a key did;
these record what the system did on its own, which is what a pipeline waits
for and a dashboard reacts to. A run finishing is nobody's action.

Published from the worker because that is where a run actually ends. The API
can start one and can ask for it to stop, but the moment it is over is decided
here, by the reconciler, after the generators are gone.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from plimsoll_api.messaging import WEBHOOK_DELIVERIES, get_bus

logger = logging.getLogger(__name__)

RUN_COMPLETED = "run.completed"
RUN_FAILED = "run.failed"
RUN_SLA_BREACHED = "run.sla_breached"


async def announce_run(
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    project_id: uuid.UUID | None,
    event: str,
    detail: dict[str, str] | None = None,
) -> None:
    """Offer the event onward without letting delivery decide anything.

    A run that has finished has finished. A broker that is down must not turn
    that into a run that failed, so a publish that cannot happen is logged and
    dropped -- the database is the record, and this is a copy leaving.
    """
    try:
        await get_bus().publish(
            WEBHOOK_DELIVERIES,
            {
                "organizationId": str(org_id),
                "event": event,
                "entityType": "run",
                "entityId": str(run_id),
                "projectId": str(project_id) if project_id else "",
                "actorId": "",
                "occurredAt": datetime.now(UTC).isoformat(),
                **(detail or {}),
            },
        )
    except Exception:
        logger.exception("could not queue %s for run %s", event, run_id)
