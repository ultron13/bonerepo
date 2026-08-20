"""API keys, stored as hashes."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

_COLUMNS = "id, name, prefix, scopes, last_used_at, expires_at, revoked_at, created_by, created_at "


async def insert(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    name: str,
    key_hash: str,
    prefix: str,
    scopes: list[str],
    expires_at: datetime | None,
    created_by: uuid.UUID | None,
) -> sa.Row[Any]:
    return (
        await session.execute(
            sa.text(
                "INSERT INTO api_keys "
                "(id, organization_id, name, key_hash, prefix, scopes, expires_at, created_by) "
                "VALUES (:id, :org, :name, :hash, :prefix, :scopes, :expires, :by) "
                "RETURNING " + _COLUMNS
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "name": name,
                "hash": key_hash,
                "prefix": prefix,
                "scopes": scopes,
                "expires": expires_at,
                "by": created_by,
            },
        )
    ).one()


async def find_usable(session: AsyncSession, key_hash: str) -> sa.Row[Any] | None:
    """Resolved through a definer function, because nothing is scoped yet.

    The key is what establishes which organisation this is, so the lookup
    cannot already be scoped to one -- and row-level security refuses an
    unscoped read, correctly. The function applies the revocation and expiry
    conditions itself, so no caller can forget them and authenticate a revoked
    credential.
    """
    return (
        await session.execute(
            sa.text("SELECT id, organization_id, scopes FROM auth_api_key(:hash)"),
            {"hash": key_hash},
        )
    ).first()


async def touch(session: AsyncSession, key_hash: str) -> None:
    """When it was last used, so an operator can tell a live pipeline from a
    forgotten one before revoking it.

    By hash rather than by identifier: the write happens before an organisation
    is established, through the same narrow definer path as the lookup.
    """
    await session.execute(sa.text("SELECT auth_touch_api_key(:hash)"), {"hash": key_hash})


async def list_for_org(session: AsyncSession) -> list[sa.Row[Any]]:
    return list(
        (
            await session.execute(
                sa.text(
                    "SELECT " + _COLUMNS + "FROM api_keys "
                    "WHERE revoked_at IS NULL ORDER BY created_at DESC"
                )
            )
        ).all()
    )


async def revoke(session: AsyncSession, key_id: uuid.UUID) -> bool:
    """Idempotent: revoking a revoked key is the outcome the caller wanted."""
    row = (
        await session.execute(
            sa.text(
                "UPDATE api_keys SET revoked_at = now() "
                "WHERE id = :id AND revoked_at IS NULL RETURNING id"
            ),
            {"id": key_id},
        )
    ).first()
    return row is not None
