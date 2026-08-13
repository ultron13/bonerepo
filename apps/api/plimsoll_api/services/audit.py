"""The audit trail is written inside the caller's transaction.

A trail written in a second transaction has holes in it exactly when something
went wrong -- the change rolls back and its record survives, or the reverse.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.repositories import audit as repo
from plimsoll_api.security.tokens import AccessClaims


async def record(
    session: AsyncSession,
    *,
    principal: AccessClaims,
    action: str,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    await repo.insert(
        session,
        org_id=principal.organization_id,
        user_id=principal.user_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata=metadata,
    )
