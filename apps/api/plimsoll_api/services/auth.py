from __future__ import annotations

import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.config import get_settings
from plimsoll_api.db.session import bind_session_to_org
from plimsoll_api.repositories import users as user_repo
from plimsoll_api.security.passwords import hash_password, verify_password
from plimsoll_api.security.tokens import issue_access_token
from plimsoll_api.services.refresh import issue_family

# A real hash of a value nobody knows. Verifying against it when the account
# does not exist costs the same as a genuine mismatch, so response time does
# not reveal which addresses are registered.
_DUMMY_HASH = hash_password(secrets.token_urlsafe(32))


class AuthenticationFailed(Exception):
    """Credentials did not match, or the account is not active."""


async def authenticate(session: AsyncSession, email: str, password: str) -> tuple[str, str, int]:
    """Returns (access_token, refresh_token, access_ttl_seconds).

    The session arrives unscoped, because no organisation is known until the
    user is resolved. It is bound to the principal's organisation before the
    refresh-token family is written, which is what lets that write pass the
    tenant policy.
    """
    row = await user_repo.lookup_for_login(session, email)
    if row is None or row.password_hash is None or row.status != "ACTIVE":
        verify_password(_DUMMY_HASH, password)
        raise AuthenticationFailed("Email or password is incorrect.")

    if not verify_password(row.password_hash, password):
        raise AuthenticationFailed("Email or password is incorrect.")

    await bind_session_to_org(session, row.organization_id)

    access = issue_access_token(row.id, row.organization_id, row.org_role)
    refresh = await issue_family(session, row.id, row.organization_id)
    return access, refresh, get_settings().access_token_ttl_seconds
