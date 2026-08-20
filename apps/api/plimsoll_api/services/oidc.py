"""Turning a verified identity into a session here.

The identity provider says who somebody is. This decides what that means in
this organisation, and the three decisions it makes are the ones worth stating:

* An address outside the provider's declared domains gets no account. A
  provider that has not said which domains it speaks for should not be able to
  create an account for any address at all -- otherwise a misconfigured or
  compromised provider mints accounts for addresses it has no business
  asserting.
* A deactivated user stays deactivated. Offboarding that a second sign-in route
  walks around is not offboarding, and this is exactly the route somebody would
  reach for after being removed.
* Group membership is authoritative on every sign-in, in both directions. An
  identity provider that has taken somebody out of the administrators group has
  said something, and only honouring it in the granting direction would mean
  the group controls promotion and nothing controls demotion.

The last active administrator is still protected, because a group edit at the
provider must not be able to leave an organisation with nobody who can
administer it -- including nobody who can fix the provider configuration that
caused it.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.repositories import identity_providers as repo
from plimsoll_api.repositories import users_admin
from plimsoll_api.security.oidc import Identity, OidcError

ADMIN = "ORG_ADMIN"
VIEWER = "VIEWER"


def role_for(identity: Identity, admin_group: str | None) -> str:
    """No configured admin group means nobody is promoted by signing in.

    The alternative -- treating "no group configured" as "everybody is an
    administrator" -- is the kind of default that is convenient once and wrong
    for ever after.
    """
    if admin_group and admin_group in identity.groups:
        return ADMIN
    return VIEWER


def domain_permitted(email: str, allowed: list[str]) -> bool:
    if not allowed:
        return False
    domain = email.rsplit("@", 1)[-1].lower()
    return domain in {entry.lower().lstrip("@") for entry in allowed}


async def sign_in(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    identity: Identity,
    provider: Any,
) -> tuple[uuid.UUID, str]:
    """Returns (user_id, org_role). Creates the user if this is their first
    sign-in, and reconciles their role with the provider on every one."""
    if not domain_permitted(identity.email, list(provider.allowed_domains)):
        raise OidcError(
            f"This identity provider is not configured for addresses at "
            f"{identity.email.rsplit('@', 1)[-1]}."
        )

    role = role_for(identity, provider.admin_group)
    existing = await repo.find_user_by_email(session, identity.email)

    if existing is None:
        # First sign-in. No password is set, and none can be: the account has
        # no local credential to guess, and `authenticate` already refuses a
        # user whose password_hash is NULL.
        row = await users_admin.insert(
            session,
            user_id=uuid.uuid4(),
            org_id=org_id,
            email=identity.email,
            name=identity.name,
            org_role=role,
            password_hash=None,
        )
        return uuid.UUID(str(row.id)), str(row.org_role)

    if existing.status != "ACTIVE":
        raise OidcError("This account is not active.")

    if existing.org_role != role:
        # Demotion is refused only when it would leave nobody in charge. The
        # sign-in still succeeds, with the role they had: refusing it outright
        # would lock out the one person able to correct the group.
        if (
            role != ADMIN
            and existing.org_role == ADMIN
            and await users_admin.count_active_admins(session, excluding=existing.id) == 0
        ):
            return uuid.UUID(str(existing.id)), str(existing.org_role)
        await users_admin.set_role(session, existing.id, role)
        return uuid.UUID(str(existing.id)), role

    return uuid.UUID(str(existing.id)), str(existing.org_role)
