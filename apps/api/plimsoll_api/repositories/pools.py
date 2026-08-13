"""Pool capacity is described here and derived, never counted on the pool.

A mutable current_users column updated outside a transaction boundary drifts
under concurrent runs; in-flight load comes from run_generators of active runs,
which is the same data the orchestrator acts on.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

_SELECT = (
    "SELECT id, name, runtime, config, region, max_generators, max_vus_per_generator, "
    "       supported_engines, status, created_at "
)
_RETURNING = (
    "RETURNING id, name, runtime, config, region, max_generators, max_vus_per_generator, "
    "          supported_engines, status, created_at"
)


async def insert(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    name: str,
    runtime: str,
    config: dict[str, Any],
    region: str | None,
    max_generators: int,
    max_vus_per_generator: int,
) -> sa.Row[Any]:
    return (
        await session.execute(
            sa.text(
                "INSERT INTO generator_pools "
                "(id, organization_id, name, runtime, config, region, max_generators, "
                " max_vus_per_generator) "
                "VALUES (:id, :org, :name, :runtime, CAST(:config AS jsonb), :region, "
                ":max_generators, :max_vus_per_generator) " + _RETURNING
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "name": name,
                "runtime": runtime,
                "config": json.dumps(config),
                "region": region,
                "max_generators": max_generators,
                "max_vus_per_generator": max_vus_per_generator,
            },
        )
    ).one()


async def get(session: AsyncSession, pool_id: uuid.UUID) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text(_SELECT + "FROM generator_pools WHERE id = :id"), {"id": pool_id}
        )
    ).first()


async def list_page(
    session: AsyncSession, *, limit: int, after: tuple[datetime, uuid.UUID] | None
) -> list[sa.Row[Any]]:
    parameters: dict[str, Any] = {"limit": limit}
    if after is None:
        statement = sa.text(
            _SELECT + "FROM generator_pools ORDER BY created_at DESC, id DESC LIMIT :limit"
        )
    else:
        statement = sa.text(
            _SELECT + "FROM generator_pools WHERE (created_at, id) < (:after_at, :after_id) "
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        )
        parameters["after_at"], parameters["after_id"] = after
    return list((await session.execute(statement, parameters)).all())


async def update(
    session: AsyncSession, pool_id: uuid.UUID, changes: dict[str, Any]
) -> sa.Row[Any] | None:
    # Column names come from the service's fixed set, never from a client key;
    # values remain bound parameters.
    assignments = ", ".join(f"{column} = :{column}" for column in changes)
    bound = dict(changes)
    if "config" in bound:
        # A dict bound straight into a jsonb column raises; it arrives as text
        # and is cast, exactly as it is on insert.
        bound["config"] = json.dumps(bound["config"])
        assignments = assignments.replace("config = :config", "config = CAST(:config AS jsonb)")
    return (
        await session.execute(
            sa.text(
                f"UPDATE generator_pools SET {assignments}, updated_at = now() "  # noqa: S608
                "WHERE id = :id " + _RETURNING
            ),
            {**bound, "id": pool_id},
        )
    ).first()


async def archive(session: AsyncSession, pool_id: uuid.UUID) -> bool:
    row = (
        await session.execute(
            sa.text(
                "UPDATE generator_pools SET status = 'ARCHIVED', updated_at = now() "
                "WHERE id = :id AND status <> 'ARCHIVED' RETURNING id"
            ),
            {"id": pool_id},
        )
    ).first()
    return row is not None


async def capacity(session: AsyncSession, pool_id: uuid.UUID) -> int | None:
    value: int | None = await session.scalar(
        sa.text(
            "SELECT max_generators * max_vus_per_generator FROM generator_pools "
            "WHERE id = :id AND status = 'ACTIVE'"
        ),
        {"id": pool_id},
    )
    return value


async def committed_users(session: AsyncSession, pool_id: uuid.UUID) -> int:
    """Virtual users held by generators of runs that have not finished.

    Returns zero until S3 creates runs, and starts telling the truth then
    without this query changing.
    """
    value: int | None = await session.scalar(
        sa.text(
            "SELECT COALESCE(SUM(rg.assigned_users), 0) FROM run_generators rg "
            "JOIN test_runs r ON r.id = rg.run_id "
            "WHERE rg.pool_id = :id "
            "  AND r.status IN ('QUEUED', 'STARTING', 'RUNNING', 'STOPPING')"
        ),
        {"id": pool_id},
    )
    return value or 0
