from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.errors import PlimsollError
from plimsoll_api.git.client import GitAccess
from plimsoll_api.repositories import credentials as credentials_repo
from plimsoll_api.repositories import script_repos as repo
from plimsoll_api.security.secrets import get_key_provider
from plimsoll_api.security.tokens import AccessClaims
from plimsoll_api.services import audit
from plimsoll_api.services import projects as projects_service
from plimsoll_contracts.errors import ErrorCode
from plimsoll_contracts.scripts import ScriptRepoCreate, ScriptRepoUpdate

UPDATABLE = {"name", "plan_path", "default_ref", "credential_id"}


async def create(
    session: AsyncSession,
    principal: AccessClaims,
    project_id: uuid.UUID,
    body: ScriptRepoCreate,
) -> Any:
    # A project in another organisation is invisible, so this 404s before the
    # repository is written against an identifier the caller cannot see.
    await projects_service.require(session, project_id)
    if body.credential_id is not None:
        if await credentials_repo.get(session, body.credential_id) is None:
            raise PlimsollError(
                ErrorCode.VALIDATION_FAILED,
                "No such credential.",
                {"credentialId": str(body.credential_id)},
            )

    row = await repo.insert(
        session,
        org_id=principal.organization_id,
        project_id=project_id,
        created_by=principal.user_id,
        name=body.name,
        repo_url=body.repo_url,
        plan_path=body.plan_path,
        default_ref=body.default_ref,
        credential_id=body.credential_id,
    )
    await audit.record(
        session,
        principal=principal,
        action="script_repo.created",
        entity_type="script_repo",
        entity_id=row.id,
        metadata={"repoUrl": body.repo_url, "planPath": body.plan_path},
    )
    return row


async def require(session: AsyncSession, repo_id: uuid.UUID) -> Any:
    row = await repo.get(session, repo_id)
    if row is None:
        raise PlimsollError(ErrorCode.NOT_FOUND, "No such script repository.")
    return row


async def update(
    session: AsyncSession,
    principal: AccessClaims,
    repo_id: uuid.UUID,
    body: ScriptRepoUpdate,
) -> Any:
    current = await require(session, repo_id)
    changes = {
        column: value
        for column, value in body.model_dump(exclude_unset=True).items()
        if column in UPDATABLE
    }
    if not changes:
        return current
    if changes.get("credential_id") is not None:
        if await credentials_repo.get(session, changes["credential_id"]) is None:
            raise PlimsollError(ErrorCode.VALIDATION_FAILED, "No such credential.")

    row = await repo.update(session, repo_id, changes)
    await audit.record(
        session,
        principal=principal,
        action="script_repo.updated",
        entity_type="script_repo",
        entity_id=repo_id,
        metadata={"fields": sorted(changes)},
    )
    return row


async def archive(session: AsyncSession, principal: AccessClaims, repo_id: uuid.UUID) -> None:
    await require(session, repo_id)
    if await repo.archive(session, repo_id):
        await audit.record(
            session,
            principal=principal,
            action="script_repo.deleted",
            entity_type="script_repo",
            entity_id=repo_id,
        )


async def access_for(session: AsyncSession, row: Any) -> GitAccess:
    """The one place a credential is decrypted for Git.

    Callers hold the result only as long as the operation takes, and it never
    reaches a DTO.
    """
    if row.credential_id is None:
        return GitAccess(url=row.repo_url)

    material = await credentials_repo.secret_material(session, row.credential_id)
    if material is None:
        raise PlimsollError(
            ErrorCode.VALIDATION_FAILED, "The repository's credential no longer exists."
        )
    return GitAccess(
        url=row.repo_url,
        kind=material.kind,
        secret=get_key_provider().decrypt(material.ciphertext, material.key_ref),
    )
