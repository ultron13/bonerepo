"""Keyset pagination on (created_at, id).

Offset pagination skips or duplicates rows when inserts land mid-scan, which on
a control plane whose history only grows is always.

The column list is spelled out in each statement rather than interpolated from
a constant: parameterised SQL only, and an f-string in a query is the habit
that eventually interpolates something a client sent.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def insert(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    created_by: uuid.UUID,
    name: str,
    project_key: str,
    description: str | None,
    environment: str | None,
    tags: list[str],
) -> sa.Row[Any]:
    return (
        await session.execute(
            sa.text(
                "INSERT INTO projects "
                "(id, organization_id, name, project_key, description, environment, tags, "
                " created_by) "
                "VALUES (:id, :org, :name, :key, :description, :environment, :tags, :by) "
                "RETURNING id, name, project_key, description, environment, status, tags, "
                "          created_at, updated_at"
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "name": name,
                "key": project_key,
                "description": description,
                "environment": environment,
                "tags": tags,
                "by": created_by,
            },
        )
    ).one()


async def get(session: AsyncSession, project_id: uuid.UUID) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text(
                "SELECT id, name, project_key, description, environment, status, tags, "
                "       created_at, updated_at "
                "FROM projects WHERE id = :id"
            ),
            {"id": project_id},
        )
    ).first()


async def list_page(
    session: AsyncSession, *, limit: int, after: tuple[datetime, uuid.UUID] | None
) -> list[sa.Row[Any]]:
    """(created_at, id) < (:after_at, :after_id) is a row-value comparison; two
    ANDed inequalities would drop or repeat rows sharing a timestamp."""
    parameters: dict[str, Any] = {"limit": limit}
    if after is None:
        statement = sa.text(
            "SELECT id, name, project_key, description, environment, status, tags, "
            "       created_at, updated_at "
            "FROM projects ORDER BY created_at DESC, id DESC LIMIT :limit"
        )
    else:
        statement = sa.text(
            "SELECT id, name, project_key, description, environment, status, tags, "
            "       created_at, updated_at "
            "FROM projects WHERE (created_at, id) < (:after_at, :after_id) "
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        )
        parameters["after_at"], parameters["after_id"] = after
    return list((await session.execute(statement, parameters)).all())


async def update(
    session: AsyncSession, project_id: uuid.UUID, changes: dict[str, Any]
) -> sa.Row[Any] | None:
    # `assignments` names columns from the service's fixed UPDATABLE set, never
    # keys a client sent; the values themselves are still bound parameters.
    assignments = ", ".join(f"{column} = :{column}" for column in changes)
    return (
        await session.execute(
            sa.text(
                f"UPDATE projects SET {assignments}, updated_at = now() "  # noqa: S608
                "WHERE id = :id "
                "RETURNING id, name, project_key, description, environment, status, tags, "
                "          created_at, updated_at"
            ),
            {**changes, "id": project_id},
        )
    ).first()


async def archive(session: AsyncSession, project_id: uuid.UUID) -> bool:
    """Returns whether this call was the one that archived it, so a repeat
    writes no second audit row."""
    row = (
        await session.execute(
            sa.text(
                "UPDATE projects SET status = 'ARCHIVED', updated_at = now() "
                "WHERE id = :id AND status <> 'ARCHIVED' RETURNING id"
            ),
            {"id": project_id},
        )
    ).first()
    return row is not None
