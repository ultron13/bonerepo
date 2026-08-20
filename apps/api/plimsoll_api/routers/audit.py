"""Reading the audit trail.

Writing it is not the point; producing it is. An operator asked to account for
what happened to a run, or what a departing administrator did, answers from
here.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from plimsoll_api.dependencies import TenantSession
from plimsoll_api.pagination import page_of, position_from
from plimsoll_api.repositories import audit as repo
from plimsoll_api.security.permissions import Permission, requires
from plimsoll_contracts.audit import AuditEntry
from plimsoll_contracts.pagination import DEFAULT_PAGE_LIMIT, MAX_PAGE_LIMIT, Page

router = APIRouter(prefix="/api/v1/audit-logs", tags=["audit"])


def _response(row: Any) -> AuditEntry:
    return AuditEntry(
        id=row.id,
        user_id=row.user_id,
        api_key_id=row.api_key_id,
        action=row.action,
        entity_type=row.entity_type,
        entity_id=row.entity_id,
        # inet comes back as an address object; the wire carries text.
        ip_address=str(row.ip_address) if row.ip_address is not None else None,
        metadata=row.metadata,
        created_at=row.created_at,
    )


@router.get(
    "",
    response_model=Page[AuditEntry],
    # Administrative, not a read like any other: the trail names people, and a
    # viewer is not entitled to an account of what colleagues did.
    dependencies=[Depends(requires(Permission.ADMIN_SYSTEM))],
)
async def list_audit_logs(
    session: TenantSession,
    limit: int = Query(default=DEFAULT_PAGE_LIMIT, ge=1, le=MAX_PAGE_LIMIT),
    cursor: str | None = None,
    action: Annotated[str | None, Query(max_length=255)] = None,
    entity_type: Annotated[str | None, Query(alias="entityType", max_length=100)] = None,
    entity_id: Annotated[uuid.UUID | None, Query(alias="entityId")] = None,
    user_id: Annotated[uuid.UUID | None, Query(alias="userId")] = None,
) -> Page[AuditEntry]:
    """Newest first. Every filter narrows; none of them widens past the
    organisation, which row-level security fixes rather than this query."""
    rows = await repo.list_page(
        session,
        limit=limit + 1,
        after=position_from(cursor),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        user_id=user_id,
    )
    return page_of(rows, limit, _response)
