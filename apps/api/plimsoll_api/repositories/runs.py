"""Runs, and the generators allocated to them.

Every status change is a conditional update returning the row it changed. A
caller that gets None lost a race -- to another worker, or to a stop that
arrived first -- and must not assume its transition happened.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

_COLUMNS = (
    "id, project_id, performance_test_id, run_number, status, trigger_source, "
    "started_at, ended_at, initiated_by, configuration_snapshot, summary, "
    "sla_result, degraded, created_at "
)
_SELECT = "SELECT " + _COLUMNS
_RETURNING = "RETURNING " + _COLUMNS


async def next_run_number(session: AsyncSession, project_id: uuid.UUID) -> int:
    """The UPDATE takes the counter row's lock, so two concurrent starts
    serialise here rather than racing UNIQUE (project_id, run_number). A
    rolled-back creation gives its number back, which a sequence would not.

    The column holds the number to hand out next, defaulting to 1, so the
    statement consumes it and returns what it consumed rather than the new
    value it left behind.
    """
    await session.execute(
        sa.text(
            "INSERT INTO project_run_counters (project_id, organization_id) "
            "SELECT id, organization_id FROM projects WHERE id = :project "
            "ON CONFLICT (project_id) DO NOTHING"
        ),
        {"project": project_id},
    )
    number: int | None = await session.scalar(
        sa.text(
            "UPDATE project_run_counters SET next_run_number = next_run_number + 1 "
            "WHERE project_id = :project RETURNING next_run_number - 1"
        ),
        {"project": project_id},
    )
    if number is None:
        raise RuntimeError("The project has no run counter row.")
    return number


async def insert(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    test_id: uuid.UUID,
    run_number: int,
    initiated_by: uuid.UUID | None,
    trigger_source: str,
    snapshot: dict[str, Any],
) -> sa.Row[Any]:
    return (
        await session.execute(
            sa.text(
                "INSERT INTO test_runs "
                "(id, organization_id, project_id, performance_test_id, run_number, status, "
                " trigger_source, initiated_by, configuration_snapshot) "
                "VALUES (:id, :org, :project, :test, :number, 'QUEUED', :trigger, :by, "
                "        CAST(:snapshot AS jsonb)) " + _RETURNING
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "project": project_id,
                "test": test_id,
                "number": run_number,
                "trigger": trigger_source,
                "by": initiated_by,
                "snapshot": json.dumps(snapshot),
            },
        )
    ).one()


async def get(session: AsyncSession, run_id: uuid.UUID) -> sa.Row[Any] | None:
    return (
        await session.execute(sa.text(_SELECT + "FROM test_runs WHERE id = :id"), {"id": run_id})
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
            _SELECT + "FROM test_runs WHERE project_id = :project "
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        )
    else:
        statement = sa.text(
            _SELECT + "FROM test_runs WHERE project_id = :project "
            "  AND (created_at, id) < (:after_at, :after_id) "
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        )
        parameters["after_at"], parameters["after_id"] = after
    return list((await session.execute(statement, parameters)).all())


async def transition(
    session: AsyncSession,
    run_id: uuid.UUID,
    *,
    expected: list[str],
    to: str,
    started: bool = False,
    ended: bool = False,
) -> sa.Row[Any] | None:
    """None means the run was not in `expected` -- someone else moved it."""
    return (
        await session.execute(
            sa.text(
                "UPDATE test_runs SET status = :to"  # noqa: S608
                + (", started_at = COALESCE(started_at, now())" if started else "")
                + (", ended_at = now()" if ended else "")
                + " WHERE id = :id AND status = ANY(:expected) "
                + _RETURNING
            ),
            {"id": run_id, "to": to, "expected": expected},
        )
    ).first()


async def mark_degraded(session: AsyncSession, run_id: uuid.UUID) -> None:
    await session.execute(
        sa.text("UPDATE test_runs SET degraded = TRUE WHERE id = :id"), {"id": run_id}
    )


async def set_summary(session: AsyncSession, run_id: uuid.UUID, summary: dict[str, Any]) -> None:
    await session.execute(
        sa.text("UPDATE test_runs SET summary = CAST(:summary AS jsonb) WHERE id = :id"),
        {"id": run_id, "summary": json.dumps(summary)},
    )


async def insert_generators(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    run_id: uuid.UUID,
    pool_id: uuid.UUID,
    allocation: list[int],
) -> None:
    """Written once, before anything is provisioned. The rows are what make
    provisioning idempotent: one row per ordinal, and an ordinal that already
    carries an external_ref is never provisioned again."""
    for ordinal, users in enumerate(allocation):
        await session.execute(
            sa.text(
                "INSERT INTO run_generators "
                "(id, organization_id, run_id, pool_id, ordinal, assigned_users, status) "
                "VALUES (:id, :org, :run, :pool, :ordinal, :users, 'PENDING') "
                "ON CONFLICT (run_id, ordinal) DO NOTHING"
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "run": run_id,
                "pool": pool_id,
                "ordinal": ordinal,
                "users": users,
            },
        )


async def generators_for(session: AsyncSession, run_id: uuid.UUID) -> list[sa.Row[Any]]:
    return list(
        (
            await session.execute(
                sa.text(
                    "SELECT id, ordinal, external_ref, assigned_users, status, "
                    "       last_heartbeat, started_at, ended_at "
                    "FROM run_generators WHERE run_id = :run ORDER BY ordinal"
                ),
                {"run": run_id},
            )
        ).all()
    )


async def attach_external_ref(
    session: AsyncSession, run_id: uuid.UUID, ordinal: int, external_ref: str
) -> None:
    await session.execute(
        sa.text(
            "UPDATE run_generators SET external_ref = :ref, status = 'PROVISIONED' "
            "WHERE run_id = :run AND ordinal = :ordinal AND external_ref IS NULL"
        ),
        {"run": run_id, "ordinal": ordinal, "ref": external_ref},
    )


async def record_external_ref(
    session: AsyncSession, run_id: uuid.UUID, ordinal: int, external_ref: str
) -> None:
    """Attach a reference without touching the status.

    Adoption records a container that already exists; the generator inside it
    may be further along than provisioned, and saying otherwise walks its
    lifecycle backwards into a state the run can never leave.
    """
    await session.execute(
        sa.text(
            "UPDATE run_generators SET external_ref = :ref "
            "WHERE run_id = :run AND ordinal = :ordinal AND external_ref IS NULL"
        ),
        {"run": run_id, "ordinal": ordinal, "ref": external_ref},
    )


async def set_generator_status(
    session: AsyncSession,
    run_id: uuid.UUID,
    ordinal: int,
    status: str,
    *,
    heartbeat: bool = False,
) -> None:
    await session.execute(
        sa.text(
            "UPDATE run_generators SET status = :status"  # noqa: S608
            + (", last_heartbeat = now()" if heartbeat else "")
            + " WHERE run_id = :run AND ordinal = :ordinal"
        ),
        {"run": run_id, "ordinal": ordinal, "status": status},
    )


async def touch_heartbeat(session: AsyncSession, run_id: uuid.UUID, ordinal: int) -> None:
    await session.execute(
        sa.text(
            "UPDATE run_generators SET last_heartbeat = now() "
            "WHERE run_id = :run AND ordinal = :ordinal"
        ),
        {"run": run_id, "ordinal": ordinal},
    )


async def record_bundle_digest(session: AsyncSession, run_id: uuid.UUID, digest: str) -> None:
    """Part of what the run is pinned to: the exact bytes every generator ran.

    It belongs beside the commit SHAs for the same reason they do -- a run that
    cannot say what it executed cannot be reproduced.
    """
    await session.execute(
        sa.text(
            "UPDATE test_runs "
            "SET configuration_snapshot = jsonb_set("
            "  configuration_snapshot, '{bundleSha256}', to_jsonb(CAST(:digest AS text))) "
            "WHERE id = :id"
        ),
        {"id": run_id, "digest": digest},
    )


async def record_sla_result(
    session: AsyncSession, run_id: uuid.UUID, outcome: str, detail: dict[str, Any]
) -> None:
    """The verdict in two places, because they answer different questions.

    `sla_result` is one word, as the data model types it: it is what a list of
    runs filters and sorts on. The per-rule breakdown goes into `summary`
    beside the rest of what the run reports, because it is read with the run
    rather than across runs.
    """
    await session.execute(
        sa.text(
            "UPDATE test_runs "
            "SET sla_result = :outcome, "
            "    summary = COALESCE(summary, '{}'::jsonb) || CAST(:detail AS jsonb) "
            "WHERE id = :id"
        ),
        {"id": run_id, "outcome": outcome, "detail": json.dumps({"sla": detail})},
    )
