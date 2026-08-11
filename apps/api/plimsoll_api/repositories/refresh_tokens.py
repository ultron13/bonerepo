from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def insert_family(
    session: AsyncSession,
    family_id: uuid.UUID,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    token_hash: str,
    expires_at: datetime,
) -> None:
    await session.execute(
        sa.text(
            "INSERT INTO refresh_token_families "
            "(id, organization_id, user_id, current_hash, expires_at) "
            "VALUES (:id, :org, :user, :hash, :expires)"
        ),
        {
            "id": family_id,
            "org": org_id,
            "user": user_id,
            "hash": token_hash,
            "expires": expires_at,
        },
    )


async def find_by_hash(session: AsyncSession, token_hash: str) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text(
                "SELECT id, user_id, organization_id, current_hash, revoked_at, expires_at "
                "FROM refresh_token_families WHERE current_hash = :hash"
            ),
            {"hash": token_hash},
        )
    ).first()


async def find_family(session: AsyncSession, family_id: uuid.UUID) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text("SELECT id, revoked_at FROM refresh_token_families WHERE id = :id"),
            {"id": family_id},
        )
    ).first()


async def replace_hash(session: AsyncSession, family_id: uuid.UUID, token_hash: str) -> None:
    await session.execute(
        sa.text("UPDATE refresh_token_families SET current_hash = :hash WHERE id = :id"),
        {"hash": token_hash, "id": family_id},
    )


async def revoke_family_containing(session: AsyncSession, token_hash: str) -> None:
    """Revoke by *previous* hash. Reuse of a consumed token is the theft signal."""
    await session.execute(
        sa.text(
            "UPDATE refresh_token_families SET revoked_at = now() "
            "WHERE id = (SELECT family_id FROM refresh_token_history WHERE token_hash = :hash)"
        ),
        {"hash": token_hash},
    )


async def organization_for_token(session: AsyncSession, token_hash: str) -> uuid.UUID | None:
    """Resolve the organisation of a presented token before any scope is set.

    Goes through the SECURITY DEFINER function: the family and history tables
    are policy-protected, and a refresh request carries no principal yet.
    """
    org_id: uuid.UUID | None = await session.scalar(
        sa.text("SELECT auth_org_for_refresh(:hash)"), {"hash": token_hash}
    )
    return org_id


async def record_history(
    session: AsyncSession, family_id: uuid.UUID, org_id: uuid.UUID, token_hash: str
) -> None:
    await session.execute(
        sa.text(
            "INSERT INTO refresh_token_history (token_hash, family_id, organization_id) "
            "VALUES (:hash, :family, :org)"
        ),
        {"hash": token_hash, "family": family_id, "org": org_id},
    )
