"""A test is one document: workload, plans, and SLA rules together.

The documented API has no endpoints for plans or rules, and a rule that
outlives the test it constrains is a bug waiting to be found, so they are
replaced atomically under the optimistic-lock guard.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.errors import PlimsollError
from plimsoll_api.repositories import performance_tests as repo
from plimsoll_api.repositories import pools as pools_repo
from plimsoll_api.repositories import script_repos as script_repos_repo
from plimsoll_api.security.tokens import AccessClaims
from plimsoll_api.services import audit
from plimsoll_api.services import projects as projects_service
from plimsoll_contracts.errors import ErrorCode
from plimsoll_contracts.performance_tests import (
    SlaRuleSpec,
    TestCreate,
    TestPlanSpec,
    TestUpdate,
    WorkloadSpec,
)

UPDATABLE = {"name", "description", "configuration", "tags"}


@dataclass(frozen=True)
class TestDocument:
    row: Any
    plans: list[TestPlanSpec]
    sla_rules: list[SlaRuleSpec]


async def _check_references(
    session: AsyncSession, configuration: WorkloadSpec, plans: list[TestPlanSpec]
) -> None:
    """Every unknown reference is collected, so a caller fixes the set rather
    than one identifier per request."""
    problems: dict[str, Any] = {}
    if await pools_repo.get(session, configuration.generator_pool_id) is None:
        problems["generatorPoolId"] = str(configuration.generator_pool_id)

    missing = [
        str(plan.script_repo_id)
        for plan in plans
        if await script_repos_repo.get(session, plan.script_repo_id) is None
    ]
    if missing:
        problems["scriptRepoIds"] = missing

    if problems:
        raise PlimsollError(
            ErrorCode.VALIDATION_FAILED,
            "The test refers to resources that do not exist.",
            problems,
        )


async def _document(session: AsyncSession, row: Any) -> TestDocument:
    plans = [
        TestPlanSpec(
            script_repo_id=plan.script_repo_id,
            pinned_ref=plan.pinned_ref,
            virtual_users=plan.virtual_users,
            execution_order=plan.execution_order,
        )
        for plan in await repo.plans_for(session, row.id)
    ]
    rules = [
        SlaRuleSpec(
            name=rule.name,
            metric=rule.metric,
            entity=rule.entity,
            operator=rule.operator,
            threshold=rule.threshold,
            unit=rule.unit,
            severity=rule.severity,
            enabled=rule.enabled,
        )
        for rule in await repo.sla_rules_for(session, row.id)
    ]
    return TestDocument(row=row, plans=plans, sla_rules=rules)


async def create(
    session: AsyncSession, principal: AccessClaims, project_id: uuid.UUID, body: TestCreate
) -> TestDocument:
    await projects_service.require(session, project_id)
    await _check_references(session, body.configuration, body.plans)

    row = await repo.insert(
        session,
        org_id=principal.organization_id,
        project_id=project_id,
        created_by=principal.user_id,
        name=body.name,
        description=body.description,
        configuration=body.configuration.model_dump(mode="json"),
        tags=body.tags,
    )
    await repo.replace_plans(
        session, org_id=principal.organization_id, test_id=row.id, plans=body.plans
    )
    await repo.replace_sla_rules(
        session, org_id=principal.organization_id, test_id=row.id, rules=body.sla_rules
    )
    await audit.record(
        session,
        principal=principal,
        action="test.created",
        entity_type="performance_test",
        entity_id=row.id,
        metadata={"plans": len(body.plans), "slaRules": len(body.sla_rules)},
    )
    return await _document(session, row)


async def require(session: AsyncSession, test_id: uuid.UUID) -> TestDocument:
    row = await repo.get(session, test_id)
    if row is None:
        raise PlimsollError(ErrorCode.NOT_FOUND, "No such performance test.")
    return await _document(session, row)


async def update(
    session: AsyncSession, principal: AccessClaims, test_id: uuid.UUID, body: TestUpdate
) -> TestDocument:
    current = await require(session, test_id)

    if body.configuration is not None or body.plans is not None:
        await _check_references(
            session,
            body.configuration or WorkloadSpec.model_validate(current.row.configuration),
            body.plans if body.plans is not None else current.plans,
        )

    changes: dict[str, Any] = {}
    for column, value in body.model_dump(exclude_unset=True, mode="json").items():
        if column in UPDATABLE:
            changes[column] = value
    if "configuration" in changes:
        changes["configuration"] = json.dumps(changes["configuration"])

    row = await repo.update_document(session, test_id, body.version, changes)
    if row is None:
        raise PlimsollError(
            ErrorCode.CONFLICT,
            "This test was modified by someone else; reload it and reapply your change.",
            {"expectedVersion": body.version},
        )

    # Absent means leave alone; an empty list means clear.
    if body.plans is not None:
        await repo.replace_plans(
            session, org_id=principal.organization_id, test_id=test_id, plans=body.plans
        )
    if body.sla_rules is not None:
        await repo.replace_sla_rules(
            session, org_id=principal.organization_id, test_id=test_id, rules=body.sla_rules
        )

    await audit.record(
        session,
        principal=principal,
        action="test.updated",
        entity_type="performance_test",
        entity_id=test_id,
        metadata={"fields": sorted(changes), "version": row.version},
    )
    return await _document(session, row)


async def archive(session: AsyncSession, principal: AccessClaims, test_id: uuid.UUID) -> None:
    await require(session, test_id)
    if await repo.archive(session, test_id):
        await audit.record(
            session,
            principal=principal,
            action="test.deleted",
            entity_type="performance_test",
            entity_id=test_id,
        )
