from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query

from plimsoll_api.allocation import CapacityError, allocate
from plimsoll_api.db.session import session_for_org
from plimsoll_api.dependencies import CurrentPrincipal, TenantSession
from plimsoll_api.errors import PlimsollError
from plimsoll_api.messaging import RUNS_EXECUTION, get_bus, run_channel
from plimsoll_api.pagination import page_of, position_from
from plimsoll_api.repositories import runs as repo
from plimsoll_api.security.permissions import Permission, requires
from plimsoll_api.services import performance_tests, pools, preflight, target_policy
from plimsoll_api.services import runs as service
from plimsoll_contracts.agent import Command
from plimsoll_contracts.errors import ErrorCode
from plimsoll_contracts.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, Page
from plimsoll_contracts.performance_tests import WorkloadSpec
from plimsoll_contracts.runs import GeneratorView, RunResponse, RunStatusResponse

router = APIRouter(tags=["runs"])


def _response(row: Any) -> RunResponse:
    return RunResponse(
        id=row.id,
        project_id=row.project_id,
        performance_test_id=row.performance_test_id,
        run_number=row.run_number,
        status=row.status,
        trigger_source=row.trigger_source,
        degraded=row.degraded,
        started_at=row.started_at,
        ended_at=row.ended_at,
        created_at=row.created_at,
        configuration_snapshot=row.configuration_snapshot,
        summary=row.summary,
    )


@router.post(
    "/api/v1/tests/{test_id}/runs",
    response_model=RunResponse,
    status_code=201,
    dependencies=[Depends(requires(Permission.TEST_EXECUTE))],
)
async def start_run(test_id: uuid.UUID, principal: CurrentPrincipal) -> RunResponse:
    """Preflight runs first and refuses the whole run, listing every failure.

    No session is held while it talks to Git, so this endpoint opens three short
    transactions rather than one long one.
    """
    async with session_for_org(principal.organization_id) as session:
        inputs = await preflight.gather(session, test_id)
        document = await performance_tests.require(session, test_id)
        workload = WorkloadSpec.model_validate(document.row.configuration)
        pool = await pools.require(session, workload.generator_pool_id)
        policy = await target_policy.current_policy(session)
        sla_rules = [rule.model_dump(mode="json") for rule in document.sla_rules]

    assessment = await preflight.assess(inputs)
    if not assessment.report.ok:
        raise PlimsollError(
            ErrorCode.TEST_NOT_RUNNABLE,
            "This test cannot run yet.",
            {"checks": [check.model_dump(mode="json") for check in assessment.report.checks]},
        )

    try:
        allocation = allocate(
            total_users=workload.virtual_users,
            max_generators=pool.max_generators,
            max_vus_per_generator=pool.max_vus_per_generator,
        )
    except CapacityError as exc:
        raise PlimsollError(ErrorCode.INSUFFICIENT_CAPACITY, str(exc)) from exc

    snapshot = service.build_snapshot(
        inputs,
        assessment,
        workload=workload,
        allocation=allocation,
        target_policy_version=policy.version if policy is not None else 0,
        sla_rules=sla_rules,
    )

    async with session_for_org(principal.organization_id) as session:
        row = await service.create(session, principal, test_id, snapshot)

    # After the commit: a transaction is never held open across a network call.
    try:
        await get_bus().publish(
            RUNS_EXECUTION,
            {"runId": str(row.id), "organizationId": str(principal.organization_id)},
        )
    # Any broker failure is the same failure: the run is not going to be picked up.
    except Exception as exc:
        async with session_for_org(principal.organization_id) as session:
            await repo.transition(session, row.id, expected=["QUEUED"], to="FAILED", ended=True)
            await repo.set_summary(session, row.id, {"error": "The run could not be queued."})
        raise PlimsollError(
            ErrorCode.INTERNAL, "The run could not be queued; nothing was started."
        ) from exc

    return _response(row)


@router.get(
    "/api/v1/projects/{project_id}/runs",
    response_model=Page[RunResponse],
    dependencies=[Depends(requires(Permission.TEST_READ))],
)
async def list_runs(
    project_id: uuid.UUID,
    session: TenantSession,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    cursor: str | None = None,
) -> Page[RunResponse]:
    rows = await repo.list_page_for_project(
        session, project_id, limit=limit + 1, after=position_from(cursor)
    )
    return page_of(rows, limit, _response)


@router.get(
    "/api/v1/runs/{run_id}",
    response_model=RunResponse,
    dependencies=[Depends(requires(Permission.TEST_READ))],
)
async def get_run(run_id: uuid.UUID, session: TenantSession) -> RunResponse:
    return _response(await service.require(session, run_id))


@router.get(
    "/api/v1/runs/{run_id}/status",
    response_model=RunStatusResponse,
    dependencies=[Depends(requires(Permission.TEST_READ))],
)
async def get_run_status(run_id: uuid.UUID, session: TenantSession) -> RunStatusResponse:
    row = await service.require(session, run_id)
    generators = await repo.generators_for(session, run_id)
    return RunStatusResponse(
        id=row.id,
        status=row.status,
        degraded=row.degraded,
        started_at=row.started_at,
        ended_at=row.ended_at,
        generators=[
            GeneratorView(
                ordinal=generator.ordinal,
                status=generator.status,
                assigned_users=generator.assigned_users,
                last_heartbeat=generator.last_heartbeat,
            )
            for generator in generators
        ],
    )


@router.post(
    "/api/v1/runs/{run_id}/stop",
    response_model=RunResponse,
    dependencies=[Depends(requires(Permission.TEST_EXECUTE))],
)
async def stop_run(run_id: uuid.UUID, principal: CurrentPrincipal) -> RunResponse:
    """Wind the run down: the load stops, and what ran still counts."""
    async with session_for_org(principal.organization_id) as session:
        row = await service.request_stop(session, principal, run_id, cancel=False)
    await get_bus().announce(run_channel(run_id), {"command": Command.STOP.value})
    return _response(row)


@router.post(
    "/api/v1/runs/{run_id}/cancel",
    response_model=RunResponse,
    dependencies=[Depends(requires(Permission.TEST_EXECUTE))],
)
async def cancel_run(run_id: uuid.UUID, principal: CurrentPrincipal) -> RunResponse:
    """Abandon the run: it ends now, and its result is not a result."""
    async with session_for_org(principal.organization_id) as session:
        row = await service.request_stop(session, principal, run_id, cancel=True)
    await get_bus().announce(run_channel(run_id), {"command": Command.CANCEL.value})
    return _response(row)
