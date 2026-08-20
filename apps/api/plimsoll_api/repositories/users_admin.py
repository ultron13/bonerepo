"""Reads and writes of the user directory, all organisation-scoped.

Separate from `repositories.users`, which serves authentication and runs
before an organisation is known. Everything here runs inside a tenant session,
so row-level security is what confines it rather than a WHERE clause.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def list_users(session: AsyncSession) -> list[sa.Row[Any]]:
    return list(
        (
            await session.execute(
                sa.text(
                    "SELECT id, email, name, org_role, status, created_at "
                    "FROM users ORDER BY created_at, id"
                )
            )
        ).all()
    )


async def get(session: AsyncSession, user_id: uuid.UUID) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text(
                "SELECT id, email, name, org_role, status, created_at FROM users WHERE id = :id"
            ),
            {"id": user_id},
        )
    ).first()


async def insert(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    email: str,
    name: str,
    org_role: str,
    # None for a user who signs in through an identity provider: there is no
    # local credential, and `authenticate` refuses a NULL password_hash.
    password_hash: str | None,
) -> sa.Row[Any]:
    return (
        await session.execute(
            sa.text(
                "INSERT INTO users "
                "(id, organization_id, email, name, org_role, password_hash, status) "
                "VALUES (:id, :org, lower(:email), :name, :role, :hash, 'ACTIVE') "
                "RETURNING id, email, name, org_role, status, created_at"
            ),
            {
                "id": user_id,
                "org": org_id,
                "email": email,
                "name": name,
                "role": org_role,
                "hash": password_hash,
            },
        )
    ).one()


async def set_role(session: AsyncSession, user_id: uuid.UUID, org_role: str) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text(
                "UPDATE users SET org_role = :role, updated_at = now() "
                "WHERE id = :id "
                "RETURNING id, email, name, org_role, status, created_at"
            ),
            {"id": user_id, "role": org_role},
        )
    ).first()


async def set_status(session: AsyncSession, user_id: uuid.UUID, status: str) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text(
                "UPDATE users SET status = :status, updated_at = now() "
                "WHERE id = :id "
                "RETURNING id, email, name, org_role, status, created_at"
            ),
            {"id": user_id, "status": status},
        )
    ).first()


async def count_active_admins(session: AsyncSession, *, excluding: uuid.UUID) -> int:
    """How many administrators would remain without this one."""
    return int(
        (
            await session.execute(
                sa.text(
                    "SELECT count(*) FROM users "
                    "WHERE org_role = 'ORG_ADMIN' AND status = 'ACTIVE' AND id <> :id"
                ),
                {"id": excluding},
            )
        ).scalar_one()
    )


async def revoke_all_refresh_tokens(session: AsyncSession, user_id: uuid.UUID) -> None:
    """Every live session this user holds, ended now.

    Without this, deactivation only stops new sign-ins while existing refresh
    tokens keep minting access tokens for their full lifetime.
    """
    await session.execute(
        sa.text(
            "UPDATE refresh_token_families SET revoked_at = now() "
            "WHERE user_id = :id AND revoked_at IS NULL"
        ),
        {"id": user_id},
    )
