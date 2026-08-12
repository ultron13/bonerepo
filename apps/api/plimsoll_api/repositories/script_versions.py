from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

_SELECT = "SELECT id, script_repo_id, commit_sha, plan_path, checksum, metadata, resolved_at "


async def insert(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    script_repo_id: uuid.UUID,
    commit_sha: str,
    plan_path: str,
    checksum: str,
    metadata: dict[str, Any],
) -> sa.Row[Any]:
    return (
        await session.execute(
            sa.text(
                "INSERT INTO script_versions "
                "(id, organization_id, script_repo_id, commit_sha, plan_path, checksum, metadata) "
                "VALUES (:id, :org, :repo, :sha, :path, :checksum, CAST(:metadata AS jsonb)) "
                "RETURNING id, script_repo_id, commit_sha, plan_path, checksum, metadata, "
                "          resolved_at"
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "repo": script_repo_id,
                "sha": commit_sha,
                "path": plan_path,
                "checksum": checksum,
                "metadata": json.dumps(metadata),
            },
        )
    ).one()


async def by_commit(
    session: AsyncSession, script_repo_id: uuid.UUID, commit_sha: str, plan_path: str
) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text(
                _SELECT + "FROM script_versions "
                "WHERE script_repo_id = :repo AND commit_sha = :sha AND plan_path = :path"
            ),
            {"repo": script_repo_id, "sha": commit_sha, "path": plan_path},
        )
    ).first()


async def get(session: AsyncSession, version_id: uuid.UUID) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text(_SELECT + "FROM script_versions WHERE id = :id"), {"id": version_id}
        )
    ).first()


async def list_page_for_repo(
    session: AsyncSession,
    script_repo_id: uuid.UUID,
    *,
    limit: int,
    after: tuple[datetime, uuid.UUID] | None,
) -> list[sa.Row[Any]]:
    """Ordered by resolved_at, which is this table's creation timestamp."""
    parameters: dict[str, Any] = {"limit": limit, "repo": script_repo_id}
    if after is None:
        statement = sa.text(
            _SELECT + "FROM script_versions WHERE script_repo_id = :repo "
            "ORDER BY resolved_at DESC, id DESC LIMIT :limit"
        )
    else:
        statement = sa.text(
            _SELECT + "FROM script_versions WHERE script_repo_id = :repo "
            "  AND (resolved_at, id) < (:after_at, :after_id) "
            "ORDER BY resolved_at DESC, id DESC LIMIT :limit"
        )
        parameters["after_at"], parameters["after_id"] = after
    return list((await session.execute(statement, parameters)).all())
