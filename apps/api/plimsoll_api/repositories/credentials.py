"""Reads come in two shapes: the four columns the API may show, and the
material only server-side callers may hold. Keeping them in separate queries
means a DTO cannot accidentally be built from a row carrying ciphertext.
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
    kind: str,
    ciphertext: bytes,
    key_ref: str,
) -> sa.Row[Any]:
    return (
        await session.execute(
            sa.text(
                "INSERT INTO credentials "
                "(id, organization_id, name, kind, ciphertext, key_ref, created_by) "
                "VALUES (:id, :org, :name, :kind, :ciphertext, :key_ref, :by) "
                "RETURNING id, name, kind, created_at"
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "name": name,
                "kind": kind,
                "ciphertext": ciphertext,
                "key_ref": key_ref,
                "by": created_by,
            },
        )
    ).one()


async def get(session: AsyncSession, credential_id: uuid.UUID) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text("SELECT id, name, kind, created_at FROM credentials WHERE id = :id"),
            {"id": credential_id},
        )
    ).first()


async def list_page(
    session: AsyncSession, *, limit: int, after: tuple[datetime, uuid.UUID] | None
) -> list[sa.Row[Any]]:
    parameters: dict[str, Any] = {"limit": limit}
    if after is None:
        statement = sa.text(
            "SELECT id, name, kind, created_at FROM credentials "
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        )
    else:
        statement = sa.text(
            "SELECT id, name, kind, created_at FROM credentials "
            "WHERE (created_at, id) < (:after_at, :after_id) "
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        )
        parameters["after_at"], parameters["after_id"] = after
    return list((await session.execute(statement, parameters)).all())


async def secret_material(session: AsyncSession, credential_id: uuid.UUID) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text("SELECT ciphertext, key_ref, kind FROM credentials WHERE id = :id"),
            {"id": credential_id},
        )
    ).first()


async def by_name(session: AsyncSession, name: str) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text("SELECT id, ciphertext, key_ref, kind FROM credentials WHERE name = :name"),
            {"name": name},
        )
    ).first()


async def referencing_repos(session: AsyncSession, credential_id: uuid.UUID) -> list[str]:
    result = await session.execute(
        sa.text("SELECT name FROM script_repos WHERE credential_id = :id ORDER BY name"),
        {"id": credential_id},
    )
    return [row.name for row in result.all()]


async def delete(session: AsyncSession, credential_id: uuid.UUID) -> bool:
    row = (
        await session.execute(
            sa.text("DELETE FROM credentials WHERE id = :id RETURNING id"), {"id": credential_id}
        )
    ).first()
    return row is not None
