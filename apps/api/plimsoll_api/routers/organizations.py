"""Creating an organisation, which is an operator's act rather than a user's.

Every other route here belongs to somebody inside an organisation. This one
brings one into being, so there is nobody inside it yet to authorise it -- the
same shape as running a migration or rotating a key, and it is authenticated
the same way those are: with a credential the operator configures, not one the
product issues.

Deliberately not a role. A `SUPER_ADMIN` that could reach across organisations
would need a way past row-level security, and row-level security is the tenant
boundary (invariant 4). A token that can create an empty organisation and its
first administrator cannot read anybody's data, and that is a much smaller
thing to hold.

The endpoint does not exist unless the token is configured. Absent means
refused, never open: a default would be a way into every deployment that never
set one.
"""

from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter, Header

from plimsoll_api.config import get_settings
from plimsoll_api.db.session import session_for_org
from plimsoll_api.errors import PlimsollError
from plimsoll_api.repositories import organizations as repo
from plimsoll_api.security.passwords import hash_password
from plimsoll_contracts.errors import ErrorCode
from plimsoll_contracts.organizations import OrganizationCreate, OrganizationCreated

router = APIRouter(prefix="/api/v1/organizations", tags=["organizations"])


def _require_instance_token(authorization: str | None) -> None:
    configured = get_settings().instance_token
    if not configured:
        raise PlimsollError(
            ErrorCode.PERMISSION_DENIED,
            "This deployment does not accept organisation creation over the API. "
            "Set PLIMSOLL_INSTANCE_TOKEN to enable it.",
        )
    presented = (authorization or "").removeprefix("Bearer ").strip()
    # Constant time, because a token compared with == leaks its prefix to
    # anybody willing to measure.
    if not presented or not secrets.compare_digest(presented, configured):
        raise PlimsollError(ErrorCode.UNAUTHENTICATED, "The instance token is not valid.")


@router.post("", response_model=OrganizationCreated, status_code=201)
async def create_organization(
    body: OrganizationCreate, authorization: str | None = Header(default=None)
) -> OrganizationCreated:
    """An organisation and the one administrator who can then fill it.

    Created together on purpose. An organisation with nobody in it can only be
    entered by making somebody, and doing that is the same privilege as this
    -- so the two would always be used together, and separating them would
    only mean a window in which a tenant existed and was unreachable.
    """
    _require_instance_token(authorization)

    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    password = secrets.token_urlsafe(24)

    # Scoped to the organisation being created, because every tenant table is
    # FORCEd -- including for the row that establishes the tenant.
    async with session_for_org(org_id) as session:
        if await repo.slug_taken(session, body.slug):
            raise PlimsollError(
                ErrorCode.CONFLICT, f"An organisation already uses the slug {body.slug!r}."
            )
        await repo.insert(session, org_id=org_id, name=body.name, slug=body.slug)
        await repo.insert_first_admin(
            session,
            user_id=user_id,
            org_id=org_id,
            email=str(body.admin_email),
            name=body.admin_name,
            password_hash=hash_password(password),
        )

    return OrganizationCreated(
        id=str(org_id),
        name=body.name,
        slug=body.slug,
        admin_email=str(body.admin_email),
        admin_temporary_password=password,
    )
