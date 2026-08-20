"""Inviting people, changing what they may do, and removing them."""

from __future__ import annotations

import secrets
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.errors import PlimsollError
from plimsoll_api.repositories import users_admin as repo
from plimsoll_api.security.passwords import hash_password
from plimsoll_contracts.errors import ErrorCode
from plimsoll_contracts.users import UserInvite

ACTIVE = "ACTIVE"
SUSPENDED = "SUSPENDED"


def _temporary_password() -> str:
    """Long enough that it does not need an expiry to be safe to hand over."""
    return secrets.token_urlsafe(24)


async def invite(
    session: AsyncSession, org_id: uuid.UUID, body: UserInvite
) -> tuple[sa.Row[Any], str]:
    password = _temporary_password()
    try:
        row = await repo.insert(
            session,
            user_id=uuid.uuid4(),
            org_id=org_id,
            email=str(body.email),
            name=body.name,
            org_role=body.org_role,
            password_hash=hash_password(password),
        )
    except IntegrityError as exc:
        # 23505 is unique_violation. The only unique constraint reachable here
        # is (organization_id, email), so a duplicate is a person who is
        # already a member rather than an internal fault.
        if getattr(exc.orig, "sqlstate", None) == "23505":
            raise PlimsollError(
                ErrorCode.CONFLICT,
                "Somebody with that email address is already in this organisation.",
            ) from exc
        raise
    return row, password


async def change_role(session: AsyncSession, user_id: uuid.UUID, org_role: str) -> sa.Row[Any]:
    """Demoting the last administrator is refused for the same reason
    deactivating them is: it would leave the organisation unadministrable."""
    current = await repo.get(session, user_id)
    if current is None:
        raise PlimsollError(ErrorCode.NOT_FOUND, "No such user in this organisation.")
    if (
        current.org_role == "ORG_ADMIN"
        and org_role != "ORG_ADMIN"
        and await repo.count_active_admins(session, excluding=user_id) == 0
    ):
        raise PlimsollError(
            ErrorCode.VALIDATION_FAILED,
            "This is the last active administrator. Promote somebody else first.",
        )
    row = await repo.set_role(session, user_id, org_role)
    assert row is not None  # The row was read a moment ago inside this transaction.
    return row


async def deactivate(session: AsyncSession, user_id: uuid.UUID) -> sa.Row[Any]:
    current = await repo.get(session, user_id)
    if current is None:
        raise PlimsollError(ErrorCode.NOT_FOUND, "No such user in this organisation.")
    if (
        current.org_role == "ORG_ADMIN"
        and await repo.count_active_admins(session, excluding=user_id) == 0
    ):
        raise PlimsollError(
            ErrorCode.VALIDATION_FAILED,
            "This is the last active administrator. Promote somebody else first.",
        )
    row = await repo.set_status(session, user_id, SUSPENDED)
    # The status blocks a new sign-in; this ends the sessions that already
    # exist. Both are needed, and the second is the one that is easy to miss.
    await repo.revoke_all_refresh_tokens(session, user_id)
    assert row is not None
    return row


async def reactivate(session: AsyncSession, user_id: uuid.UUID) -> sa.Row[Any]:
    row = await repo.set_status(session, user_id, ACTIVE)
    if row is None:
        raise PlimsollError(ErrorCode.NOT_FOUND, "No such user in this organisation.")
    return row
