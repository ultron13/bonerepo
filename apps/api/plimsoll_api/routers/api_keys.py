"""Managing the credentials that belong to pipelines rather than people."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Response

from plimsoll_api.config import get_settings
from plimsoll_api.dependencies import CurrentPrincipal, TenantSession
from plimsoll_api.errors import PlimsollError
from plimsoll_api.repositories import api_keys as repo
from plimsoll_api.security.api_keys import mint
from plimsoll_api.security.permissions import Permission, held_by, requires
from plimsoll_api.services import audit
from plimsoll_contracts.api_keys import ApiKeyCreate, ApiKeyCreated, ApiKeyResponse
from plimsoll_contracts.errors import ErrorCode
from plimsoll_contracts.pagination import Page

router = APIRouter(prefix="/api/v1/api-keys", tags=["api-keys"])


def _response(row: Any) -> ApiKeyResponse:
    return ApiKeyResponse(
        id=row.id,
        name=row.name,
        prefix=row.prefix,
        scopes=list(row.scopes),
        last_used_at=row.last_used_at,
        expires_at=row.expires_at,
        created_at=row.created_at,
    )


@router.post(
    "",
    response_model=ApiKeyCreated,
    status_code=201,
    # Issuing credentials is administrative. Key sprawl nobody can account for
    # is its own problem, and the subset check below is a second limit rather
    # than the only one.
    dependencies=[Depends(requires(Permission.ADMIN_SYSTEM))],
)
async def create_api_key(
    body: ApiKeyCreate, principal: CurrentPrincipal, session: TenantSession
) -> ApiKeyCreated:
    """A key may hold no more than the person creating it.

    Without that, a key is a privilege-escalation tool: a viewer mints an
    administrator and the roles mean nothing. There is deliberately no
    permission that lets someone grant what they do not have.
    """
    known = {permission.value for permission in Permission}
    unknown = sorted(set(body.scopes) - known)
    if unknown:
        raise PlimsollError(ErrorCode.VALIDATION_FAILED, f"Unknown scopes: {', '.join(unknown)}.")

    holds = {permission.value for permission in held_by(principal)}
    excess = sorted(set(body.scopes) - holds)
    if excess:
        raise PlimsollError(
            ErrorCode.PERMISSION_DENIED,
            f"A key cannot be granted more than you hold: {', '.join(excess)}.",
        )

    secret, key_hash, prefix = mint(get_settings().environment)
    expires_at = (
        datetime.now(UTC) + timedelta(days=body.expires_in_days)
        if body.expires_in_days is not None
        else None
    )
    row = await repo.insert(
        session,
        org_id=principal.organization_id,
        name=body.name,
        key_hash=key_hash,
        prefix=prefix,
        scopes=sorted(set(body.scopes)),
        expires_at=expires_at,
        created_by=principal.user_id,
    )
    await audit.record(
        session,
        principal=principal,
        action="api_key.created",
        entity_type="api_key",
        entity_id=row.id,
        metadata={"name": body.name, "scopes": sorted(set(body.scopes))},
    )
    # Built from the row rather than round-tripped through the response
    # model: that dump carries serialisation aliases, which are not the field
    # names it would be reconstructed from.
    return ApiKeyCreated(
        **_response(row).model_dump(by_alias=False),
        secret=secret,
    )


@router.get(
    "",
    response_model=Page[ApiKeyResponse],
    dependencies=[Depends(requires(Permission.ADMIN_SYSTEM))],
)
async def list_api_keys(session: TenantSession) -> Page[ApiKeyResponse]:
    """Never the secret. There is no path that returns one twice."""
    rows = await repo.list_for_org(session)
    return Page(items=[_response(row) for row in rows])


@router.delete(
    "/{key_id}",
    status_code=204,
    # Revoking a key breaks whatever was using it. Without this guard anyone
    # who could read could stop every pipeline in the organisation.
    dependencies=[Depends(requires(Permission.ADMIN_SYSTEM))],
)
async def revoke_api_key(
    key_id: uuid.UUID, principal: CurrentPrincipal, session: TenantSession
) -> Response:
    """Idempotent. Revoking a revoked key is the outcome the caller wanted, and
    a pipeline broken twice by an error is worse than one broken once."""
    revoked = await repo.revoke(session, key_id)
    if revoked:
        await audit.record(
            session,
            principal=principal,
            action="api_key.revoked",
            entity_type="api_key",
            entity_id=key_id,
        )
    return Response(status_code=204)
