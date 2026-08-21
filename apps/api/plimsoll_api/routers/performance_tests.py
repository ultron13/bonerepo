from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response

from plimsoll_api.db.session import session_for_org
from plimsoll_api.dependencies import CurrentPrincipal, TenantSession
from plimsoll_api.errors import PlimsollError
from plimsoll_api.pagination import page_of, position_from
from plimsoll_api.repositories import performance_tests as repo
from plimsoll_api.security.permissions import Permission, requires
from plimsoll_api.services import (
    audit,
    credentials,
    pools,
    preflight,
    script_repos,
    target_policy,
)
from plimsoll_api.services import performance_tests as service
from plimsoll_contracts.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, Page
from plimsoll_contracts.performance_tests import (
    TestCreate,
    TestResponse,
    TestUpdate,
    WorkloadSpec,
)
from plimsoll_contracts.validation import CheckStatus, PreflightReport

router = APIRouter(tags=["tests"])


def _response(document: service.TestDocument) -> TestResponse:
    row = document.row
    return TestResponse(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        description=row.description,
        status=row.status,
        configuration=WorkloadSpec.model_validate(row.configuration),
        plans=document.plans,
        sla_rules=document.sla_rules,
        tags=list(row.tags),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post(
    "/api/v1/projects/{project_id}/tests",
    response_model=TestResponse,
    status_code=201,
    dependencies=[Depends(requires(Permission.TEST_WRITE))],
)
async def create_test(
    project_id: uuid.UUID,
    body: TestCreate,
    principal: CurrentPrincipal,
    session: TenantSession,
) -> TestResponse:
    return _response(await service.create(session, principal, project_id, body))


@router.get(
    "/api/v1/projects/{project_id}/tests",
    response_model=Page[TestResponse],
    dependencies=[Depends(requires(Permission.TEST_READ))],
)
async def list_tests(
    project_id: uuid.UUID,
    session: TenantSession,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    cursor: str | None = None,
) -> Page[TestResponse]:
    rows = await repo.list_page_for_project(
        session, project_id, limit=limit + 1, after=position_from(cursor)
    )
    # Two extra queries per test on the page. At the 50/200 page limits that is
    # cheap, and it keeps a listed test identical to a fetched one.
    documents = {row.id: await service.require(session, row.id) for row in rows[:limit]}
    return page_of(rows, limit, lambda row: _response(documents[row.id]))


@router.get(
    "/api/v1/tests/{test_id}",
    response_model=TestResponse,
    dependencies=[Depends(requires(Permission.TEST_READ))],
)
async def get_test(test_id: uuid.UUID, session: TenantSession) -> TestResponse:
    return _response(await service.require(session, test_id))


@router.patch(
    "/api/v1/tests/{test_id}",
    response_model=TestResponse,
    dependencies=[Depends(requires(Permission.TEST_WRITE))],
)
async def update_test(
    test_id: uuid.UUID, body: TestUpdate, principal: CurrentPrincipal, session: TenantSession
) -> TestResponse:
    return _response(await service.update(session, principal, test_id, body))


@router.delete(
    "/api/v1/tests/{test_id}",
    status_code=204,
    dependencies=[Depends(requires(Permission.TEST_WRITE))],
)
async def delete_test(
    test_id: uuid.UUID, principal: CurrentPrincipal, session: TenantSession
) -> Response:
    await service.archive(session, principal, test_id)
    return Response(status_code=204)


async def _preflight_input(session: TenantSession, test_id: uuid.UUID) -> preflight.PreflightInput:
    """Everything the checks need, read in one transaction and nothing more.

    The Git work runs after this returns, outside the transaction: a clone held
    open against a stranger's host would hold a connection with it.
    """
    document = await service.require(session, test_id)
    configuration = WorkloadSpec.model_validate(document.row.configuration)

    plans = []
    for plan in document.plans:
        row = await script_repos.require(session, plan.script_repo_id)
        plans.append(
            preflight.PlanInput(
                repo_name=row.name,
                access=await script_repos.access_for(session, row),
                plan_path=row.plan_path,
                ref=plan.pinned_ref or row.default_ref,
                virtual_users=plan.virtual_users,
            )
        )

    policy = await target_policy.current_policy(session)
    try:
        free_capacity: int | None = await pools.capacity_for(
            session, configuration.generator_pool_id
        )
    except PlimsollError:
        # An archived or deleted pool is a capacity failure to report, not a
        # reason to refuse the caller an answer about everything else.
        free_capacity = None

    return preflight.PreflightInput(
        requested_users=configuration.virtual_users,
        plans=plans,
        allowlist=list(policy.allowlist) if policy is not None else [],
        variables=await credentials.variables(session),
        free_capacity=free_capacity,
    )


@router.post(
    "/api/v1/tests/{test_id}/validate",
    response_model=PreflightReport,
    dependencies=[Depends(requires(Permission.TEST_WRITE))],
)
async def validate_test(test_id: uuid.UUID, principal: CurrentPrincipal) -> PreflightReport:
    """200 whenever the test exists: the report is the answer.

    TEST_WRITE rather than a read permission, because it makes the platform
    reach a third-party Git host on the caller's behalf.
    """
    async with session_for_org(principal.organization_id) as session:
        inputs = await _preflight_input(session, test_id)

    report = await preflight.run(inputs)

    async with session_for_org(principal.organization_id) as session:
        await audit.record(
            session,
            principal=principal,
            action="test.validated",
            entity_type="performance_test",
            entity_id=test_id,
            metadata={
                "ok": report.ok,
                "failed": [
                    check.code for check in report.checks if check.status is not CheckStatus.PASS
                ],
            },
        )
    return report
