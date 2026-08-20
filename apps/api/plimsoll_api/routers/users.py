"""The user directory: who is in this organisation and what they may do."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends

from plimsoll_api.dependencies import CurrentPrincipal, TenantSession
from plimsoll_api.repositories import users_admin as repo
from plimsoll_api.security.permissions import Permission, requires
from plimsoll_api.services import audit
from plimsoll_api.services import users as service
from plimsoll_contracts.pagination import Page
from plimsoll_contracts.users import User, UserInvite, UserInvited, UserUpdate

# Everything here is administrative. The list names people and states what each
# of them can do, which is not something a viewer needs and is exactly what
# somebody deciding whom to target would want.
router = APIRouter(
    prefix="/api/v1/users",
    tags=["users"],
    dependencies=[Depends(requires(Permission.ADMIN_SYSTEM))],
)


def _response(row: Any) -> User:
    return User(
        id=str(row.id),
        email=row.email,
        name=row.name,
        org_role=row.org_role,
        status=row.status,
        created_at=row.created_at,
    )


@router.post("", response_model=UserInvited, status_code=201)
async def invite_user(
    body: UserInvite, principal: CurrentPrincipal, session: TenantSession
) -> UserInvited:
    """The password is returned once and never stored in the clear."""
    row, password = await service.invite(session, principal.organization_id, body)
    await audit.record(
        session,
        principal=principal,
        action="user.invited",
        entity_type="user",
        entity_id=row.id,
        metadata={"email": str(body.email), "orgRole": body.org_role},
    )
    return UserInvited(
        **_response(row).model_dump(by_alias=False),
        temporary_password=password,
    )


@router.get("", response_model=Page[User])
async def list_users(session: TenantSession) -> Page[User]:
    """Scoped by row-level security, not by a filter written here."""
    return Page(items=[_response(row) for row in await repo.list_users(session)])


@router.patch("/{user_id}", response_model=User)
async def update_user(
    user_id: uuid.UUID, body: UserUpdate, principal: CurrentPrincipal, session: TenantSession
) -> User:
    row = await service.change_role(session, user_id, body.org_role)
    await audit.record(
        session,
        principal=principal,
        action="user.role_changed",
        entity_type="user",
        entity_id=user_id,
        metadata={"orgRole": body.org_role},
    )
    return _response(row)


@router.post("/{user_id}/deactivate", response_model=User)
async def deactivate_user(
    user_id: uuid.UUID, principal: CurrentPrincipal, session: TenantSession
) -> User:
    """Ends the sessions this user already holds, not only their next sign-in."""
    row = await service.deactivate(session, user_id)
    await audit.record(
        session,
        principal=principal,
        action="user.deactivated",
        entity_type="user",
        entity_id=user_id,
    )
    return _response(row)


@router.post("/{user_id}/reactivate", response_model=User)
async def reactivate_user(
    user_id: uuid.UUID, principal: CurrentPrincipal, session: TenantSession
) -> User:
    row = await service.reactivate(session, user_id)
    await audit.record(
        session,
        principal=principal,
        action="user.reactivated",
        entity_type="user",
        entity_id=user_id,
    )
    return _response(row)
