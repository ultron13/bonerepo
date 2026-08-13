from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Response

from plimsoll_api.dependencies import CurrentPrincipal, TenantSession
from plimsoll_api.pagination import page_of, position_from
from plimsoll_api.repositories import projects as repo
from plimsoll_api.security.permissions import Permission, requires
from plimsoll_api.services import projects as service
from plimsoll_contracts.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, Page
from plimsoll_contracts.projects import ProjectCreate, ProjectResponse, ProjectUpdate

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


def _response(row: Any) -> ProjectResponse:
    return ProjectResponse(
        id=row.id,
        name=row.name,
        project_key=row.project_key,
        description=row.description,
        environment=row.environment,
        status=row.status,
        tags=list(row.tags),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=201,
    dependencies=[Depends(requires(Permission.PROJECT_WRITE))],
)
async def create_project(
    body: ProjectCreate, principal: CurrentPrincipal, session: TenantSession
) -> ProjectResponse:
    return _response(await service.create(session, principal, body))


@router.get(
    "",
    response_model=Page[ProjectResponse],
    dependencies=[Depends(requires(Permission.PROJECT_READ))],
)
async def list_projects(
    session: TenantSession,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    cursor: str | None = None,
) -> Page[ProjectResponse]:
    rows = await repo.list_page(session, limit=limit + 1, after=position_from(cursor))
    return page_of(rows, limit, _response)


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    dependencies=[Depends(requires(Permission.PROJECT_READ))],
)
async def get_project(project_id: uuid.UUID, session: TenantSession) -> ProjectResponse:
    return _response(await service.require(session, project_id))


@router.patch(
    "/{project_id}",
    response_model=ProjectResponse,
    dependencies=[Depends(requires(Permission.PROJECT_WRITE))],
)
async def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> ProjectResponse:
    return _response(await service.update(session, principal, project_id, body))


@router.delete(
    "/{project_id}", status_code=204, dependencies=[Depends(requires(Permission.PROJECT_WRITE))]
)
async def delete_project(
    project_id: uuid.UUID, principal: CurrentPrincipal, session: TenantSession
) -> Response:
    await service.archive(session, principal, project_id)
    return Response(status_code=204)
