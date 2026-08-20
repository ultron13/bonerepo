from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

_SELECT = (
    "SELECT id, project_id, name, engine, repo_url, default_ref, plan_path, credential_id, "
    "       status, created_at, updated_at "
)
_RETURNING = (
    "RETURNING id, project_id, name, engine, repo_url, default_ref, plan_path, credential_id, "
    "          status, created_at, updated_at"
)


async def insert(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    created_by: uuid.UUID | None,
    name: str,
    repo_url: str,
    plan_path: str,
    default_ref: str,
    credential_id: uuid.UUID | None,
) -> sa.Row[Any]:
    return (
        await session.execute(
            sa.text(
                "INSERT INTO script_repos "
                "(id, organization_id, project_id, name, repo_url, default_ref, plan_path, "
                " credential_id, created_by) "
                "VALUES (:id, :org, :project, :name, :url, :ref, :path, :credential, :by) "
                + _RETURNING
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "project": project_id,
                "name": name,
                "url": repo_url,
                "ref": default_ref,
                "path": plan_path,
                "credential": credential_id,
                "by": created_by,
            },
        )
    ).one()


async def get(session: AsyncSession, repo_id: uuid.UUID) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text(_SELECT + "FROM script_repos WHERE id = :id"), {"id": repo_id}
        )
    ).first()


async def list_page_for_project(
    session: AsyncSession,
    project_id: uuid.UUID,
    *,
    limit: int,
    after: tuple[datetime, uuid.UUID] | None,
) -> list[sa.Row[Any]]:
    parameters: dict[str, Any] = {"limit": limit, "project": project_id}
    if after is None:
        statement = sa.text(
            _SELECT + "FROM script_repos WHERE project_id = :project "
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        )
    else:
        statement = sa.text(
            _SELECT + "FROM script_repos WHERE project_id = :project "
            "  AND (created_at, id) < (:after_at, :after_id) "
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        )
        parameters["after_at"], parameters["after_id"] = after
    return list((await session.execute(statement, parameters)).all())


async def update(
    session: AsyncSession, repo_id: uuid.UUID, changes: dict[str, Any]
) -> sa.Row[Any] | None:
    # Column names come from the service's fixed set, never from a client key;
    # values remain bound parameters.
    assignments = ", ".join(f"{column} = :{column}" for column in changes)
    return (
        await session.execute(
            sa.text(
                f"UPDATE script_repos SET {assignments}, updated_at = now() "  # noqa: S608
                "WHERE id = :id " + _RETURNING
            ),
            {**changes, "id": repo_id},
        )
    ).first()


async def archive(session: AsyncSession, repo_id: uuid.UUID) -> bool:
    row = (
        await session.execute(
            sa.text(
                "UPDATE script_repos SET status = 'ARCHIVED', updated_at = now() "
                "WHERE id = :id AND status <> 'ARCHIVED' RETURNING id"
            ),
            {"id": repo_id},
        )
    ).first()
    return row is not None
