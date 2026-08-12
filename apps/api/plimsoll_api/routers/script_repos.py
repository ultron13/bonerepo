"""Script repositories, and the operations that reach out to Git.

`verify` and version pinning do network work, so they take no session
dependency: they read what they need in one transaction, close it, talk to Git,
then open a second transaction to write. A transaction held open across a clone
would hold a connection for as long as a stranger's Git host takes to answer.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Response

from plimsoll_api.dependencies import CurrentPrincipal, TenantSession
from plimsoll_api.pagination import page_of, position_from
from plimsoll_api.repositories import script_repos as repo
from plimsoll_api.security.permissions import Permission, requires
from plimsoll_api.services import script_repos as service
from plimsoll_contracts.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, Page
from plimsoll_contracts.scripts import (
    ScriptRepoCreate,
    ScriptRepoResponse,
    ScriptRepoUpdate,
)

router = APIRouter(tags=["scripts"])


def _response(row: Any) -> ScriptRepoResponse:
    return ScriptRepoResponse(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        engine=row.engine,
        repo_url=row.repo_url,
        default_ref=row.default_ref,
        plan_path=row.plan_path,
        credential_id=row.credential_id,
        status=row.status,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post(
    "/api/v1/projects/{project_id}/script-repos",
    response_model=ScriptRepoResponse,
    status_code=201,
    dependencies=[Depends(requires(Permission.SCRIPT_WRITE))],
)
async def create_script_repo(
    project_id: uuid.UUID,
    body: ScriptRepoCreate,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> ScriptRepoResponse:
    return _response(await service.create(session, principal, project_id, body))


@router.get(
    "/api/v1/projects/{project_id}/script-repos",
    response_model=Page[ScriptRepoResponse],
    dependencies=[Depends(requires(Permission.SCRIPT_READ))],
)
async def list_script_repos(
    project_id: uuid.UUID,
    session: TenantSession,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    cursor: str | None = None,
) -> Page[ScriptRepoResponse]:
    rows = await repo.list_page_for_project(
        session, project_id, limit=limit + 1, after=position_from(cursor)
    )
    return page_of(rows, limit, _response)


@router.get(
    "/api/v1/script-repos/{repo_id}",
    response_model=ScriptRepoResponse,
    dependencies=[Depends(requires(Permission.SCRIPT_READ))],
)
async def get_script_repo(repo_id: uuid.UUID, session: TenantSession) -> ScriptRepoResponse:
    return _response(await service.require(session, repo_id))


@router.patch(
    "/api/v1/script-repos/{repo_id}",
    response_model=ScriptRepoResponse,
    dependencies=[Depends(requires(Permission.SCRIPT_WRITE))],
)
async def update_script_repo(
    repo_id: uuid.UUID,
    body: ScriptRepoUpdate,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> ScriptRepoResponse:
    return _response(await service.update(session, principal, repo_id, body))


@router.delete(
    "/api/v1/script-repos/{repo_id}",
    status_code=204,
    dependencies=[Depends(requires(Permission.SCRIPT_WRITE))],
)
async def delete_script_repo(
    repo_id: uuid.UUID, principal: CurrentPrincipal, session: TenantSession
) -> Response:
    await service.archive(session, principal, repo_id)
    return Response(status_code=204)


# POST /api/v1/script-repos/{repo_id}/verify follows in the next commit.
