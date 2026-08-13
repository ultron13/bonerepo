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

from plimsoll_api.db.session import session_for_org
from plimsoll_api.dependencies import CurrentPrincipal, TenantSession
from plimsoll_api.errors import PlimsollError
from plimsoll_api.pagination import page_of, position_from
from plimsoll_api.repositories import script_repos as repo
from plimsoll_api.repositories import script_versions as versions_repo
from plimsoll_api.security.permissions import Permission, requires
from plimsoll_api.services import audit, script_versions
from plimsoll_api.services import script_repos as service
from plimsoll_api.services import verify as verify_service
from plimsoll_contracts.errors import ErrorCode
from plimsoll_contracts.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, Page
from plimsoll_contracts.scripts import (
    PlanSummaryResponse,
    PlanTargetResponse,
    ScriptRepoCreate,
    ScriptRepoResponse,
    ScriptRepoUpdate,
    ScriptVersionCreate,
    ScriptVersionResponse,
    VerifyReport,
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


@router.post(
    "/api/v1/script-repos/{repo_id}/verify",
    response_model=VerifyReport,
    dependencies=[Depends(requires(Permission.SCRIPT_WRITE))],
)
async def verify_script_repo(repo_id: uuid.UUID, principal: CurrentPrincipal) -> VerifyReport:
    """Requires SCRIPT_WRITE rather than a read permission: it makes the
    platform reach a third-party host on the caller's behalf, which is a side
    effect a read-only principal should not be able to trigger."""
    async with session_for_org(principal.organization_id) as session:
        row = await service.require(session, repo_id)
        access = await service.access_for(session, row)
        plan_path, ref = row.plan_path, row.default_ref

    report = await verify_service.run(access, plan_path, ref)

    async with session_for_org(principal.organization_id) as session:
        await audit.record(
            session,
            principal=principal,
            action="script_repo.verified",
            entity_type="script_repo",
            entity_id=repo_id,
            metadata={"ok": report.ok, "commitSha": report.commit_sha},
        )
    return report


def _version_response(row: Any) -> ScriptVersionResponse:
    metadata = row.metadata or {}
    summary = None
    if metadata:
        summary = PlanSummaryResponse(
            thread_groups=metadata.get("threadGroups", []),
            transaction_controllers=metadata.get("transactionControllers", []),
            timers=metadata.get("timers", []),
            variables=metadata.get("variables", []),
            data_files=metadata.get("dataFiles", []),
            targets=[PlanTargetResponse(**target) for target in metadata.get("targets", [])],
        )
    return ScriptVersionResponse(
        id=row.id,
        script_repo_id=row.script_repo_id,
        commit_sha=row.commit_sha,
        plan_path=row.plan_path,
        checksum=row.checksum,
        plan_summary=summary,
        resolved_at=row.resolved_at,
    )


@router.post(
    "/api/v1/script-repos/{repo_id}/versions",
    response_model=ScriptVersionResponse,
    dependencies=[Depends(requires(Permission.SCRIPT_WRITE))],
)
async def pin_version(
    repo_id: uuid.UUID,
    body: ScriptVersionCreate,
    principal: CurrentPrincipal,
    response: Response,
) -> ScriptVersionResponse:
    async with session_for_org(principal.organization_id) as session:
        row = await service.require(session, repo_id)
        access = await service.access_for(session, row)
        plan_path, ref = row.plan_path, body.ref or row.default_ref

    candidate = await script_versions.resolve_for_pin(access, plan_path, ref)

    async with session_for_org(principal.organization_id) as session:
        existing = await versions_repo.by_commit(session, repo_id, candidate.sha, plan_path)
        if existing is not None:
            # Already pinned: the same commit, the same plan, the same row. The
            # status code is what makes that observable to a client.
            response.status_code = 200
            return _version_response(existing)

        created = await versions_repo.insert(
            session,
            org_id=principal.organization_id,
            script_repo_id=repo_id,
            commit_sha=candidate.sha,
            plan_path=plan_path,
            checksum=candidate.checksum,
            metadata=script_versions.summary_as_metadata(candidate.summary),
        )
        await audit.record(
            session,
            principal=principal,
            action="script_version.pinned",
            entity_type="script_version",
            entity_id=created.id,
            metadata={"commitSha": candidate.sha, "ref": ref},
        )
    response.status_code = 201
    return _version_response(created)


@router.get(
    "/api/v1/script-repos/{repo_id}/versions",
    response_model=Page[ScriptVersionResponse],
    dependencies=[Depends(requires(Permission.SCRIPT_READ))],
)
async def list_versions(
    repo_id: uuid.UUID,
    session: TenantSession,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    cursor: str | None = None,
) -> Page[ScriptVersionResponse]:
    await service.require(session, repo_id)
    rows = await versions_repo.list_page_for_repo(
        session, repo_id, limit=limit + 1, after=position_from(cursor)
    )
    return page_of(rows, limit, _version_response, timestamp="resolved_at")


@router.get(
    "/api/v1/script-versions/{version_id}",
    response_model=ScriptVersionResponse,
    dependencies=[Depends(requires(Permission.SCRIPT_READ))],
)
async def get_version(version_id: uuid.UUID, session: TenantSession) -> ScriptVersionResponse:
    row = await versions_repo.get(session, version_id)
    if row is None:
        raise PlimsollError(ErrorCode.NOT_FOUND, "No such script version.")
    return _version_response(row)
