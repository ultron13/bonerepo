from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def lookup_for_login(session: AsyncSession, email: str) -> sa.Row[Any] | None:
    """Login goes through the SECURITY DEFINER function: no organisation is
    known yet, so a direct read of `users` would be filtered to nothing."""
    return (
        await session.execute(
            sa.text(
                "SELECT id, organization_id, password_hash, status, org_role "
                "FROM auth_lookup_user(:email)"
            ),
            {"email": email},
        )
    ).first()


async def get_profile(session: AsyncSession, user_id: uuid.UUID) -> sa.Row[Any] | None:
    """Reads `users` directly, so the session must already be scoped to the
    principal's organisation."""
    return (
        await session.execute(
            sa.text("SELECT id, email, name, org_role, organization_id FROM users WHERE id = :id"),
            {"id": user_id},
        )
    ).first()
