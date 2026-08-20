from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.db.session import session_for_org
from plimsoll_api.errors import PlimsollError
from plimsoll_api.repositories import api_keys as api_keys_repo
from plimsoll_api.security.api_keys import fingerprint, looks_like_a_key
from plimsoll_api.security.tokens import AccessClaims, TokenError, decode_access_token
from plimsoll_contracts.errors import ErrorCode


async def current_principal(request: Request) -> AccessClaims:
    """Who is acting, from a bearer value that is either a token or a key.

    Both arrive the same way, so the two are told apart by the key prefix
    rather than by trying to decode one as the other -- a guess that costs a
    lookup is better than a guess that costs a wrong answer.
    """
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise PlimsollError(ErrorCode.UNAUTHENTICATED, "Authentication is required.")

    presented = header.removeprefix("Bearer ")
    if looks_like_a_key(presented):
        return await _principal_for_key(presented)

    try:
        return decode_access_token(presented)
    except TokenError as exc:
        raise PlimsollError(
            ErrorCode.UNAUTHENTICATED, "The access token is invalid or has expired."
        ) from exc


async def _principal_for_key(presented: str) -> AccessClaims:
    """Looked up by hash, in a session bound to no organisation.

    The key is what establishes which organisation this is, so the lookup
    cannot already be scoped to one. Every session after this one is.
    """
    presented_hash = fingerprint(presented)
    async with session_for_org(None) as session:
        found = await api_keys_repo.find_usable(session, presented_hash)
        if found is None:
            # Revoked, expired, and unknown are one answer on purpose: telling
            # them apart tells a holder of a stale key something about it.
            raise PlimsollError(
                ErrorCode.UNAUTHENTICATED, "The API key is invalid, expired, or revoked."
            )
        await api_keys_repo.touch(session, presented_hash)

    return AccessClaims(
        user_id=None,
        organization_id=found.organization_id,
        # A key holds its scopes and nothing a role would add.
        role="API_KEY",
        api_key_id=found.id,
        scopes=frozenset(found.scopes),
    )


CurrentPrincipal = Annotated[AccessClaims, Depends(current_principal)]


async def tenant_session(principal: CurrentPrincipal) -> AsyncIterator[AsyncSession]:
    """A transaction scoped to the authenticated principal's organisation.

    The organisation comes from the verified token, never from the request.
    """
    async with session_for_org(principal.organization_id) as session:
        yield session


async def anonymous_session() -> AsyncIterator[AsyncSession]:
    """A transaction with no organisation set, so the policies expose nothing.

    Only the pre-authentication lookups reach anything through it.
    """
    async with session_for_org(None) as session:
        yield session


# scope="function" ends the dependency -- and so commits its transaction --
# before the response is sent, rather than after. Without it a client that acts
# on a response immediately can beat the commit that response implies, which
# login makes concrete: the refresh cookie names a row the next request reads.
# Every endpoint takes its session through these aliases for that reason.
TenantSession = Annotated[AsyncSession, Depends(tenant_session, scope="function")]
AnonymousSession = Annotated[AsyncSession, Depends(anonymous_session, scope="function")]
