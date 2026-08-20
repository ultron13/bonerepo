from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def insert(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    action: str,
    entity_type: str | None,
    entity_id: uuid.UUID | None,
    metadata: dict[str, Any] | None,
) -> None:
    await session.execute(
        sa.text(
            "INSERT INTO audit_logs "
            "(id, organization_id, user_id, action, entity_type, entity_id, metadata) "
            "VALUES (:id, :org, :user, :action, :entity_type, :entity_id, "
            "CAST(:metadata AS jsonb))"
        ),
        {
            "id": uuid.uuid4(),
            "org": org_id,
            "user": user_id,
            "action": action,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "metadata": json.dumps(metadata) if metadata is not None else None,
        },
    )


async def list_page(
    session: AsyncSession,
    *,
    limit: int,
    after: tuple[datetime, uuid.UUID] | None,
    action: str | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> list[sa.Row[Any]]:
    """Newest first, keyset paginated.

    An offset would drift as the trail is appended to, so a page taken while
    events are arriving would repeat or skip entries -- in an audit log, both
    are the failure it exists to prevent. The cursor compares on the same
    (created_at, id) the index is ordered by.
    """
    parameters: dict[str, Any] = {"limit": limit}
    where = ["TRUE"]

    if after is not None:
        where.append("(created_at, id) < (:after_at, :after_id)")
        parameters["after_at"], parameters["after_id"] = after
    if action is not None:
        where.append("action = :action")
        parameters["action"] = action
    if entity_type is not None:
        where.append("entity_type = :entity_type")
        parameters["entity_type"] = entity_type
    if entity_id is not None:
        where.append("entity_id = :entity_id")
        parameters["entity_id"] = entity_id
    if user_id is not None:
        where.append("user_id = :user_id")
        parameters["user_id"] = user_id

    return list(
        (
            await session.execute(
                sa.text(
                    "SELECT id, user_id, api_key_id, action, entity_type, entity_id, "
                    "       ip_address, metadata, created_at "
                    "FROM audit_logs WHERE " + " AND ".join(where) + " "
                    "ORDER BY created_at DESC, id DESC LIMIT :limit"
                ),
                parameters,
            )
        ).all()
    )
