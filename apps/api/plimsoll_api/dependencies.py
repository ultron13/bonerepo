from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.db.session import session_for_org
from plimsoll_api.errors import PlimsollError
from plimsoll_api.security.tokens import AccessClaims, TokenError, decode_access_token
from plimsoll_contracts.errors import ErrorCode


def current_principal(request: Request) -> AccessClaims:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise PlimsollError(ErrorCode.UNAUTHENTICATED, "Authentication is required.")
    try:
        return decode_access_token(header.removeprefix("Bearer "))
    except TokenError as exc:
        raise PlimsollError(
            ErrorCode.UNAUTHENTICATED, "The access token is invalid or has expired."
        ) from exc


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
