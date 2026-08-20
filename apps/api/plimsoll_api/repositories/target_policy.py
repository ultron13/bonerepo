from __future__ import annotations

import json
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def current(session: AsyncSession) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text(
                "SELECT id, version, allowlist, created_at FROM target_policies "
                "ORDER BY version DESC LIMIT 1"
            )
        )
    ).first()


async def insert_next_version(
    session: AsyncSession, *, org_id: uuid.UUID, created_by: uuid.UUID | None, allowlist: list[str]
) -> sa.Row[Any]:
    """Rows are immutable: a change is a new version, so a historical run can
    resolve what was permitted when it ran.

    The version comes from MAX(version) over rows this organisation can see,
    which row-level security has already scoped for us.
    """
    return (
        await session.execute(
            sa.text(
                "INSERT INTO target_policies "
                "(id, organization_id, version, allowlist, created_by) "
                "SELECT :id, :org, COALESCE(MAX(version), 0) + 1, CAST(:allowlist AS jsonb), :by "
                "FROM target_policies "
                "RETURNING id, version, allowlist, created_at"
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "allowlist": json.dumps(allowlist),
                "by": created_by,
            },
        )
    ).one()
