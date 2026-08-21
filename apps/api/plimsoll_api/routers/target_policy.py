from __future__ import annotations

from fastapi import APIRouter, Depends

from plimsoll_api.dependencies import CurrentPrincipal, TenantSession
from plimsoll_api.security.permissions import Permission, requires
from plimsoll_api.services import target_policy as service
from plimsoll_contracts.policy import TargetPolicyResponse, TargetPolicyUpdate

router = APIRouter(prefix="/api/v1/target-policy", tags=["policy"])


@router.get(
    "",
    response_model=TargetPolicyResponse,
    dependencies=[Depends(requires(Permission.PROJECT_READ))],
)
async def get_target_policy(session: TenantSession) -> TargetPolicyResponse:
    row = await service.current_policy(session)
    if row is None:
        # No policy and an empty policy permit exactly the same thing, so the
        # unconfigured case is reported as version 0 rather than as an error.
        return TargetPolicyResponse(version=0, allowlist=[])
    return TargetPolicyResponse(
        version=row.version, allowlist=list(row.allowlist), created_at=row.created_at
    )


@router.put(
    "",
    response_model=TargetPolicyResponse,
    dependencies=[Depends(requires(Permission.ADMIN_SYSTEM))],
)
async def replace_target_policy(
    body: TargetPolicyUpdate, principal: CurrentPrincipal, session: TenantSession
) -> TargetPolicyResponse:
    row = await service.replace(session, principal, body.allowlist)
    return TargetPolicyResponse(
        version=row.version, allowlist=list(row.allowlist), created_at=row.created_at
    )
