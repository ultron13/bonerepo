from __future__ import annotations

import json
import uuid
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
