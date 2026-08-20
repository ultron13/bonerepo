"""The identity provider configured for an organisation."""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

COLUMNS = (
    "id, organization_id, issuer, client_id, client_secret_ciphertext, "
    "client_secret_key_ref, groups_claim, admin_group, allowed_domains, enabled, created_at"
)


async def lookup_by_slug(session: AsyncSession, slug: str) -> sa.Row[Any] | None:
    """Starting a sign-in goes through the SECURITY DEFINER function: no
    organisation is known yet, so a direct read would be filtered to nothing.
    Deliberately returns no secret."""
    return (
        await session.execute(
            sa.text(
                "SELECT id, organization_id, issuer, client_id "
                "FROM auth_lookup_identity_provider(:slug)"
            ),
            {"slug": slug},
        )
    ).first()


async def get_for_org(session: AsyncSession) -> sa.Row[Any] | None:
    """Reads the table directly, so the session must already be scoped."""
    return (
        await session.execute(
            sa.text(
                "SELECT id, organization_id, issuer, client_id, client_secret_ciphertext, "
                "client_secret_key_ref, groups_claim, admin_group, allowed_domains, enabled, "
                "created_at FROM identity_providers LIMIT 1"
            )
        )
    ).first()


async def upsert(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    issuer: str,
    client_id: str,
    ciphertext: bytes,
    key_ref: str,
    groups_claim: str,
    admin_group: str | None,
    allowed_domains: list[str],
    created_by: uuid.UUID | None,
) -> sa.Row[Any]:
    return (
        await session.execute(
            sa.text(
                "INSERT INTO identity_providers "
                "(id, organization_id, issuer, client_id, client_secret_ciphertext, "
                " client_secret_key_ref, groups_claim, admin_group, allowed_domains, created_by) "
                "VALUES (:id, :org, :issuer, :client_id, :ciphertext, :key_ref, :claim, "
                "        :admin_group, :domains, :by) "
                "ON CONFLICT (organization_id) DO UPDATE SET "
                "  issuer = EXCLUDED.issuer, client_id = EXCLUDED.client_id, "
                "  client_secret_ciphertext = EXCLUDED.client_secret_ciphertext, "
                "  client_secret_key_ref = EXCLUDED.client_secret_key_ref, "
                "  groups_claim = EXCLUDED.groups_claim, admin_group = EXCLUDED.admin_group, "
                "  allowed_domains = EXCLUDED.allowed_domains, updated_at = now() "
                "RETURNING id, organization_id, issuer, client_id, groups_claim, admin_group, "
                "          allowed_domains, enabled, created_at"
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "issuer": issuer,
                "client_id": client_id,
                "ciphertext": ciphertext,
                "key_ref": key_ref,
                "claim": groups_claim,
                "admin_group": admin_group,
                "domains": allowed_domains,
                "by": created_by,
            },
        )
    ).one()


async def delete_for_org(session: AsyncSession) -> bool:
    result = await session.execute(sa.text("DELETE FROM identity_providers RETURNING id"))
    return result.first() is not None


async def find_user_by_email(session: AsyncSession, email: str) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text(
                "SELECT id, email, name, org_role, status FROM users WHERE email = lower(:email)"
            ),
            {"email": email},
        )
    ).first()
