"""Creating a run, and the snapshot that makes it reproducible."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.errors import PlimsollError
from plimsoll_api.repositories import performance_tests as tests_repo
from plimsoll_api.repositories import runs as repo
from plimsoll_api.security.tokens import AccessClaims
from plimsoll_api.services import audit
from plimsoll_api.services.preflight import Assessment, PreflightInput
from plimsoll_contracts.errors import ErrorCode
from plimsoll_contracts.performance_tests import WorkloadSpec
from plimsoll_contracts.runs import TERMINAL_RUN_STATUSES, RunStatus

TRIGGER_API = "API"


def build_snapshot(
    inputs: PreflightInput,
    assessment: Assessment,
    *,
    workload: WorkloadSpec,
    allocation: list[int],
    target_policy_version: int,
    sla_rules: list[dict[str, Any]],
) -> dict[str, Any]:
    """Everything the run needs, resolved, so nothing read after it starts
    comes from mutable configuration."""
    return {
        "plans": [
            {
                "scriptRepoId": str(plan.script_repo_id),
                "commitSha": assessment.resolved[index],
                "planPath": plan.plan_path,
                "users": plan.virtual_users,
            }
            for index, plan in enumerate(inputs.plans)
        ],
        "workload": workload.model_dump(mode="json"),
        "generators": [
            {"ordinal": ordinal, "users": users} for ordinal, users in enumerate(allocation)
        ],
        "slaRules": sla_rules,
        "targetPolicyVersion": target_policy_version,
        "allowlist": inputs.allowlist,
    }


async def create(
    session: AsyncSession,
    principal: AccessClaims,
    test_id: uuid.UUID,
    snapshot: dict[str, Any],
) -> Any:
    test = await tests_repo.get(session, test_id)
    if test is None:
        raise PlimsollError(ErrorCode.NOT_FOUND, "No such performance test.")

    run_number = await repo.next_run_number(session, test.project_id)
    row = await repo.insert(
        session,
        org_id=principal.organization_id,
        project_id=test.project_id,
        test_id=test_id,
        run_number=run_number,
        initiated_by=principal.user_id,
        trigger_source=TRIGGER_API,
        snapshot=snapshot,
    )
    await repo.insert_generators(
        session,
        org_id=principal.organization_id,
        run_id=row.id,
        pool_id=uuid.UUID(snapshot["workload"]["generatorPoolId"]),
        allocation=[generator["users"] for generator in snapshot["generators"]],
    )
    await audit.record(
        session,
        principal=principal,
        action="run.created",
        entity_type="test_run",
        entity_id=row.id,
        metadata={"runNumber": run_number, "generators": len(snapshot["generators"])},
    )
    return row


async def require(session: AsyncSession, run_id: uuid.UUID) -> Any:
    row = await repo.get(session, run_id)
    if row is None:
        raise PlimsollError(ErrorCode.NOT_FOUND, "No such run.")
    return row


async def request_stop(
    session: AsyncSession, principal: AccessClaims, run_id: uuid.UUID, *, cancel: bool
) -> Any:
    """Idempotent by construction: a state write and an announcement.

    A run already terminal is left alone and answered 200 -- repeating stop on
    a stopped run must not re-run a side effect (invariant 5).
    """
    run = await require(session, run_id)
    if run.status in TERMINAL_RUN_STATUSES:
        return run

    # Before RUNNING there is no load to wind down, so a stop is a cancel.
    abandoning = cancel or run.status != RunStatus.RUNNING
    if abandoning:
        moved = await repo.transition(
            session,
            run_id,
            expected=[
                RunStatus.QUEUED,
                RunStatus.ALLOCATING,
                RunStatus.STARTING,
                RunStatus.RUNNING,
            ],
            to=RunStatus.CANCELLED,
            ended=True,
        )
    else:
        moved = await repo.transition(
            session, run_id, expected=[RunStatus.RUNNING], to=RunStatus.STOPPING
        )
    if moved is None:
        # Someone got there first. Their transition stands; ours never happened,
        # so nothing below it runs either -- that is what makes repeating safe.
        return await require(session, run_id)

    await audit.record(
        session,
        principal=principal,
        action="run.cancelled" if cancel else "run.stopped",
        entity_type="test_run",
        entity_id=run_id,
        metadata={"from": run.status},
    )
    return moved
