from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.errors import PlimsollError
from plimsoll_api.repositories import projects as repo
from plimsoll_api.security.tokens import AccessClaims
from plimsoll_api.services import audit
from plimsoll_contracts.errors import ErrorCode
from plimsoll_contracts.projects import ProjectCreate, ProjectUpdate

UPDATABLE = {"name", "description", "environment", "tags"}


async def create(session: AsyncSession, principal: AccessClaims, body: ProjectCreate) -> Any:
    try:
        row = await repo.insert(
            session,
            org_id=principal.organization_id,
            created_by=principal.user_id,
            name=body.name,
            project_key=body.project_key,
            description=body.description,
            environment=body.environment,
            tags=body.tags,
        )
    except IntegrityError as exc:
        raise PlimsollError(
            ErrorCode.CONFLICT, f"A project with key {body.project_key} already exists."
        ) from exc
    await audit.record(
        session,
        principal=principal,
        action="project.created",
        entity_type="project",
        entity_id=row.id,
        metadata={"projectKey": body.project_key},
    )
    return row


async def require(session: AsyncSession, project_id: uuid.UUID) -> Any:
    """A project in another organisation is invisible under row-level security,
    so absent and forbidden are the same 404: confirming that an identifier
    exists elsewhere is itself a leak."""
    row = await repo.get(session, project_id)
    if row is None:
        raise PlimsollError(ErrorCode.NOT_FOUND, "No such project.")
    return row


async def update(
    session: AsyncSession, principal: AccessClaims, project_id: uuid.UUID, body: ProjectUpdate
) -> Any:
    current = await require(session, project_id)
    changes = {
        column: value
        for column, value in body.model_dump(exclude_unset=True).items()
        if column in UPDATABLE
    }
    if not changes:
        return current

    row = await repo.update(session, project_id, changes)
    await audit.record(
        session,
        principal=principal,
        action="project.updated",
        entity_type="project",
        entity_id=project_id,
        metadata={"fields": sorted(changes)},
    )
    return row


async def archive(session: AsyncSession, principal: AccessClaims, project_id: uuid.UUID) -> None:
    """Archiving an archived project writes no second audit row: repeating a
    delete has no side effect."""
    await require(session, project_id)
    if await repo.archive(session, project_id):
        await audit.record(
            session,
            principal=principal,
            action="project.deleted",
            entity_type="project",
            entity_id=project_id,
        )
