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
        # Exactly one of these is set. A person acted, or a key did, and an
        # entry that names neither answers the one question the trail exists
        # for with silence.
        user_id=principal.user_id,
        api_key_id=principal.api_key_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata=metadata,
    )


async def record_for_user(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    action: str,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """For what happens before there is a principal to attribute it to.

    Signing in is the case: the trail should record it, and at the moment it
    happens the person has no access token to be identified by. The user is
    known -- that is what just got established -- so the entry names them.
    """
    await repo.insert(
        session,
        org_id=org_id,
        user_id=user_id,
        api_key_id=None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        metadata=metadata,
    )
