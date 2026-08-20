"""Configuring an organisation's identity provider."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response

from plimsoll_api.config import get_settings
from plimsoll_api.db.session import session_for_org
from plimsoll_api.dependencies import CurrentPrincipal, TenantSession
from plimsoll_api.errors import PlimsollError
from plimsoll_api.repositories import identity_providers as repo
from plimsoll_api.security import oidc
from plimsoll_api.security.oidc import OidcError
from plimsoll_api.security.permissions import Permission, requires
from plimsoll_api.security.secrets import get_key_provider
from plimsoll_api.services import audit
from plimsoll_contracts.errors import ErrorCode
from plimsoll_contracts.identity import IdentityProvider, IdentityProviderInput

# Configuring who may sign in is the most consequential setting here, so it
# takes the highest permission and is written to the audit trail either way.
router = APIRouter(
    prefix="/api/v1/identity-provider",
    tags=["identity"],
    dependencies=[Depends(requires(Permission.ADMIN_SYSTEM))],
)


async def _slug_for(org_id: Any) -> str:
    async with session_for_org(org_id) as session:
        import sqlalchemy as sa

        return str(
            (await session.execute(sa.text("SELECT slug FROM organizations LIMIT 1"))).scalar_one()
        )


def _response(row: Any, slug: str) -> IdentityProvider:
    return IdentityProvider(
        id=str(row.id),
        issuer=row.issuer,
        client_id=row.client_id,
        groups_claim=row.groups_claim,
        admin_group=row.admin_group,
        allowed_domains=list(row.allowed_domains),
        enabled=row.enabled,
        created_at=row.created_at,
        start_url=f"{get_settings().public_api_url.rstrip('/')}/api/v1/auth/oidc/{slug}/start",
    )


@router.put("", response_model=IdentityProvider)
async def configure(
    body: IdentityProviderInput, principal: CurrentPrincipal, session: TenantSession
) -> IdentityProvider:
    """Reachability is proven before the configuration is stored.

    A provider saved without checking is one whose first failure happens to
    somebody trying to sign in, with nothing to tell them why. Discovery is
    also what confirms the issuer speaks for itself.
    """
    settings = get_settings()
    if body.issuer.startswith("http://") and not settings.oidc_allow_insecure_issuer:
        raise PlimsollError(
            ErrorCode.VALIDATION_FAILED,
            "The issuer must be an https:// URL. Anything on the path to a plain-HTTP "
            "provider can rewrite its discovery document, and with it the keys every "
            "identity token is checked against.",
        )

    try:
        await oidc.discover(body.issuer)
    except OidcError as exc:
        raise PlimsollError(ErrorCode.VALIDATION_FAILED, str(exc)) from exc

    ciphertext, key_ref = get_key_provider().encrypt(body.client_secret.encode())
    row = await repo.upsert(
        session,
        org_id=principal.organization_id,
        issuer=body.issuer,
        client_id=body.client_id,
        ciphertext=ciphertext,
        key_ref=key_ref,
        groups_claim=body.groups_claim,
        admin_group=body.admin_group,
        allowed_domains=body.allowed_domains,
        created_by=principal.user_id,
    )
    await audit.record(
        session,
        principal=principal,
        action="identity_provider.configured",
        entity_type="identity_provider",
        entity_id=row.id,
        # The issuer and the client id, never the secret.
        metadata={"issuer": body.issuer, "clientId": body.client_id},
    )
    return _response(row, await _slug_for(principal.organization_id))


@router.get("", response_model=IdentityProvider)
async def current(principal: CurrentPrincipal, session: TenantSession) -> IdentityProvider:
    """The stored secret is never returned. It was written once and is only
    read by the token exchange."""
    row = await repo.get_for_org(session)
    if row is None:
        raise PlimsollError(
            ErrorCode.NOT_FOUND, "No identity provider is configured for this organisation."
        )
    return _response(row, await _slug_for(principal.organization_id))


@router.delete("", status_code=204)
async def remove(principal: CurrentPrincipal, session: TenantSession) -> Response:
    """Idempotent: removing what is already absent is the outcome wanted."""
    removed = await repo.delete_for_org(session)
    if removed:
        await audit.record(
            session,
            principal=principal,
            action="identity_provider.removed",
            entity_type="identity_provider",
        )
    return Response(status_code=204)
