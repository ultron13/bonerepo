"""A test is one document across three tables.

The plans and rules are replaced wholesale inside the caller's transaction, so
a reader never sees a test with half its plans.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_contracts.performance_tests import SlaRuleSpec, TestPlanSpec

_SELECT = (
    "SELECT id, project_id, name, description, status, configuration, version, tags, "
    "       created_at, updated_at "
)
_RETURNING = (
    "RETURNING id, project_id, name, description, status, configuration, version, tags, "
    "          created_at, updated_at"
)


async def insert(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    project_id: uuid.UUID,
    created_by: uuid.UUID | None,
    name: str,
    description: str | None,
    configuration: dict[str, Any],
    tags: list[str],
) -> sa.Row[Any]:
    return (
        await session.execute(
            sa.text(
                "INSERT INTO performance_tests "
                "(id, organization_id, project_id, name, description, configuration, tags, "
                " created_by) "
                "VALUES (:id, :org, :project, :name, :description, CAST(:configuration AS jsonb), "
                ":tags, :by) " + _RETURNING
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "project": project_id,
                "name": name,
                "description": description,
                "configuration": json.dumps(configuration),
                "tags": tags,
                "by": created_by,
            },
        )
    ).one()


async def get(session: AsyncSession, test_id: uuid.UUID) -> sa.Row[Any] | None:
    return (
        await session.execute(
            sa.text(_SELECT + "FROM performance_tests WHERE id = :id"), {"id": test_id}
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
            _SELECT + "FROM performance_tests WHERE project_id = :project "
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        )
    else:
        statement = sa.text(
            _SELECT + "FROM performance_tests WHERE project_id = :project "
            "  AND (created_at, id) < (:after_at, :after_id) "
            "ORDER BY created_at DESC, id DESC LIMIT :limit"
        )
        parameters["after_at"], parameters["after_id"] = after
    return list((await session.execute(statement, parameters)).all())


async def update_document(
    session: AsyncSession, test_id: uuid.UUID, expected_version: int, changes: dict[str, Any]
) -> sa.Row[Any] | None:
    """No row means the version moved under us: someone else edited first."""
    # Column names come from the service's fixed set, never from a client key.
    assignments = "".join(f"{column} = :{column}, " for column in changes)
    if "configuration" in changes:
        assignments = assignments.replace(
            "configuration = :configuration", "configuration = CAST(:configuration AS jsonb)"
        )
    return (
        await session.execute(
            sa.text(
                f"UPDATE performance_tests SET {assignments}"  # noqa: S608
                "version = version + 1, updated_at = now() "
                "WHERE id = :id AND version = :expected_version " + _RETURNING
            ),
            {**changes, "id": test_id, "expected_version": expected_version},
        )
    ).first()


async def replace_plans(
    session: AsyncSession, *, org_id: uuid.UUID, test_id: uuid.UUID, plans: list[TestPlanSpec]
) -> None:
    await session.execute(
        sa.text("DELETE FROM performance_test_plans WHERE performance_test_id = :test"),
        {"test": test_id},
    )
    for plan in plans:
        await session.execute(
            sa.text(
                "INSERT INTO performance_test_plans "
                "(id, organization_id, performance_test_id, script_repo_id, pinned_ref, "
                " virtual_users, execution_order) "
                "VALUES (:id, :org, :test, :repo, :ref, :users, :order)"
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "test": test_id,
                "repo": plan.script_repo_id,
                "ref": plan.pinned_ref,
                "users": plan.virtual_users,
                "order": plan.execution_order,
            },
        )


async def replace_sla_rules(
    session: AsyncSession, *, org_id: uuid.UUID, test_id: uuid.UUID, rules: list[SlaRuleSpec]
) -> None:
    await session.execute(
        sa.text("DELETE FROM sla_rules WHERE performance_test_id = :test"), {"test": test_id}
    )
    for rule in rules:
        await session.execute(
            sa.text(
                "INSERT INTO sla_rules "
                "(id, organization_id, performance_test_id, name, metric, entity, operator, "
                " threshold, unit, severity, enabled) "
                "VALUES (:id, :org, :test, :name, :metric, :entity, :operator, :threshold, "
                ":unit, :severity, :enabled)"
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "test": test_id,
                "name": rule.name,
                "metric": str(rule.metric),
                "entity": rule.entity,
                "operator": str(rule.operator),
                "threshold": rule.threshold,
                "unit": rule.unit,
                "severity": str(rule.severity),
                "enabled": rule.enabled,
            },
        )


async def plans_for(session: AsyncSession, test_id: uuid.UUID) -> list[sa.Row[Any]]:
    result = await session.execute(
        sa.text(
            "SELECT script_repo_id, pinned_ref, virtual_users, execution_order "
            "FROM performance_test_plans WHERE performance_test_id = :test "
            "ORDER BY execution_order"
        ),
        {"test": test_id},
    )
    return list(result.all())


async def sla_rules_for(session: AsyncSession, test_id: uuid.UUID) -> list[sa.Row[Any]]:
    result = await session.execute(
        sa.text(
            "SELECT name, metric, entity, operator, threshold, unit, severity, enabled "
            "FROM sla_rules WHERE performance_test_id = :test ORDER BY name"
        ),
        {"test": test_id},
    )
    return list(result.all())


async def archive(session: AsyncSession, test_id: uuid.UUID) -> bool:
    row = (
        await session.execute(
            sa.text(
                "UPDATE performance_tests SET status = 'ARCHIVED', updated_at = now() "
                "WHERE id = :id AND status <> 'ARCHIVED' RETURNING id"
            ),
            {"id": test_id},
        )
    ).first()
    return row is not None
