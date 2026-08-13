from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Response

from plimsoll_api.dependencies import CurrentPrincipal, TenantSession
from plimsoll_api.pagination import page_of, position_from
from plimsoll_api.repositories import pools as repo
from plimsoll_api.security.permissions import Permission, requires
from plimsoll_api.services import pools as service
from plimsoll_contracts.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, Page
from plimsoll_contracts.pools import PoolCreate, PoolResponse, PoolUpdate

router = APIRouter(prefix="/api/v1/generator-pools", tags=["pools"])


def _response(row: Any) -> PoolResponse:
    return PoolResponse(
        id=row.id,
        name=row.name,
        runtime=row.runtime,
        config=row.config,
        region=row.region,
        max_generators=row.max_generators,
        max_vus_per_generator=row.max_vus_per_generator,
        capacity=row.max_generators * row.max_vus_per_generator,
        supported_engines=list(row.supported_engines),
        status=row.status,
        created_at=row.created_at,
    )


@router.post(
    "",
    response_model=PoolResponse,
    status_code=201,
    dependencies=[Depends(requires(Permission.ADMIN_SYSTEM))],
)
async def create_pool(
    body: PoolCreate, principal: CurrentPrincipal, session: TenantSession
) -> PoolResponse:
    return _response(await service.create(session, principal, body))


@router.get(
    "", response_model=Page[PoolResponse], dependencies=[Depends(requires(Permission.PROJECT_READ))]
)
async def list_pools(
    session: TenantSession,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    cursor: str | None = None,
) -> Page[PoolResponse]:
    rows = await repo.list_page(session, limit=limit + 1, after=position_from(cursor))
    return page_of(rows, limit, _response)


@router.get(
    "/{pool_id}",
    response_model=PoolResponse,
    dependencies=[Depends(requires(Permission.PROJECT_READ))],
)
async def get_pool(pool_id: uuid.UUID, session: TenantSession) -> PoolResponse:
    return _response(await service.require(session, pool_id))


@router.patch(
    "/{pool_id}",
    response_model=PoolResponse,
    dependencies=[Depends(requires(Permission.ADMIN_SYSTEM))],
)
async def update_pool(
    pool_id: uuid.UUID, body: PoolUpdate, principal: CurrentPrincipal, session: TenantSession
) -> PoolResponse:
    return _response(await service.update(session, principal, pool_id, body))


# POST /{pool_id}/test-connection needs the runtime S3 builds, and is added there.
@router.delete(
    "/{pool_id}", status_code=204, dependencies=[Depends(requires(Permission.ADMIN_SYSTEM))]
)
async def delete_pool(
    pool_id: uuid.UUID, principal: CurrentPrincipal, session: TenantSession
) -> Response:
    await service.archive(session, principal, pool_id)
    return Response(status_code=204)
