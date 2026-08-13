from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Response

from plimsoll_api.dependencies import CurrentPrincipal, TenantSession
from plimsoll_api.pagination import page_of, position_from
from plimsoll_api.repositories import credentials as repo
from plimsoll_api.security.permissions import Permission, requires
from plimsoll_api.services import credentials as service
from plimsoll_contracts.credentials import CredentialCreate, CredentialResponse
from plimsoll_contracts.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, Page

router = APIRouter(prefix="/api/v1/credentials", tags=["credentials"])


def _response(row: Any) -> CredentialResponse:
    return CredentialResponse(id=row.id, name=row.name, kind=row.kind, created_at=row.created_at)


@router.post(
    "",
    response_model=CredentialResponse,
    status_code=201,
    dependencies=[Depends(requires(Permission.ADMIN_SYSTEM))],
)
async def create_credential(
    body: CredentialCreate, principal: CurrentPrincipal, session: TenantSession
) -> CredentialResponse:
    return _response(await service.create(session, principal, body))


@router.get(
    "",
    response_model=Page[CredentialResponse],
    dependencies=[Depends(requires(Permission.SCRIPT_READ))],
)
async def list_credentials(
    session: TenantSession,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    cursor: str | None = None,
) -> Page[CredentialResponse]:
    rows = await repo.list_page(session, limit=limit + 1, after=position_from(cursor))
    return page_of(rows, limit, _response)


# There is deliberately no GET /credentials/{id}: a per-credential read is
# where a "reveal" parameter would eventually be added.
@router.delete(
    "/{credential_id}", status_code=204, dependencies=[Depends(requires(Permission.ADMIN_SYSTEM))]
)
async def delete_credential(
    credential_id: uuid.UUID, principal: CurrentPrincipal, session: TenantSession
) -> Response:
    await service.delete(session, principal, credential_id)
    return Response(status_code=204)
