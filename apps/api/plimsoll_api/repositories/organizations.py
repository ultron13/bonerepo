"""The organisation row, and the first user inside it."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def slug_taken(session: AsyncSession, slug: str) -> bool:
    """Asked through the pre-authentication lookup, because organisations are
    scoped to themselves: a session bound to the new organisation cannot see
    any other one's row, so a direct read would always say the slug is free."""
    found = (
        await session.execute(sa.text("SELECT 1 FROM auth_slug_taken(:slug)"), {"slug": slug})
    ).first()
    return found is not None


async def insert(session: AsyncSession, *, org_id: uuid.UUID, name: str, slug: str) -> None:
    await session.execute(
        sa.text("INSERT INTO organizations (id, name, slug) VALUES (:id, :name, :slug)"),
        {"id": org_id, "name": name, "slug": slug},
    )


async def insert_first_admin(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    org_id: uuid.UUID,
    email: str,
    name: str,
    password_hash: str,
) -> None:
    await session.execute(
        sa.text(
            "INSERT INTO users (id, organization_id, email, name, org_role, password_hash) "
            "VALUES (:id, :org, lower(:email), :name, 'ORG_ADMIN', :hash)"
        ),
        {"id": user_id, "org": org_id, "email": email, "name": name, "hash": password_hash},
    )
