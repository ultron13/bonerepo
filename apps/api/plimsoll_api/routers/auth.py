from __future__ import annotations

from fastapi import APIRouter, Cookie, Request, Response

from plimsoll_api.config import get_settings
from plimsoll_api.db.session import session_for_org
from plimsoll_api.dependencies import AnonymousSession, CurrentPrincipal, TenantSession
from plimsoll_api.errors import PlimsollError
from plimsoll_api.repositories import users as user_repo
from plimsoll_api.security.throttle import (
    client_address,
    record_failure,
    record_success,
    refuse_if_throttled,
)
from plimsoll_api.security.tokens import issue_access_token
from plimsoll_api.services.auth import AuthenticationFailed, authenticate
from plimsoll_api.services.refresh import RefreshRejected, organization_for, rotate
from plimsoll_contracts.auth import LoginRequest, MeResponse, TokenResponse
from plimsoll_contracts.errors import ErrorCode

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

REFRESH_COOKIE = "plimsoll_refresh"
REFRESH_COOKIE_PATH = "/api/v1/auth"


def _set_refresh_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=settings.environment != "development",
        max_age=settings.refresh_token_ttl_seconds,
        path=REFRESH_COOKIE_PATH,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AnonymousSession,
) -> TokenResponse:
    address = client_address(request)
    # Before the password is checked, so a throttled request does not take as
    # long as a real one and become a way to learn which accounts exist.
    await refuse_if_throttled(body.email, address)
    try:
        access, refresh, ttl = await authenticate(session, body.email, body.password)
    except AuthenticationFailed as exc:
        await record_failure(body.email, address)
        raise PlimsollError(ErrorCode.UNAUTHENTICATED, str(exc)) from exc
    await record_success(body.email)
    _set_refresh_cookie(response, refresh)
    return TokenResponse(access_token=access, expires_in=ttl)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(
    response: Response,
    plimsoll_refresh: str | None = Cookie(default=None),
) -> TokenResponse:
    if plimsoll_refresh is None:
        raise PlimsollError(ErrorCode.UNAUTHENTICATED, "No refresh token was presented.")

    # The token is opaque, so the organisation is resolved first through the
    # pre-authentication lookup. Everything after runs inside that scope.
    async with session_for_org(None) as session:
        org_id = await organization_for(session, plimsoll_refresh)
    if org_id is None:
        raise PlimsollError(ErrorCode.UNAUTHENTICATED, "The refresh token is not recognised.")

    async with session_for_org(org_id) as session:
        try:
            new_token, user_id, rotated_org = await rotate(session, plimsoll_refresh)
        except RefreshRejected as exc:
            raise PlimsollError(ErrorCode.UNAUTHENTICATED, str(exc)) from exc
        profile = await user_repo.get_profile(session, user_id)

    if profile is None:
        raise PlimsollError(ErrorCode.UNAUTHENTICATED, "The account no longer exists.")

    _set_refresh_cookie(response, new_token)
    settings = get_settings()
    return TokenResponse(
        access_token=issue_access_token(user_id, rotated_org, profile.org_role),
        expires_in=settings.access_token_ttl_seconds,
    )


@router.post("/logout", status_code=204)
async def logout(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH)


@router.get("/me", response_model=MeResponse)
async def me(principal: CurrentPrincipal, session: TenantSession) -> MeResponse:
    if principal.user_id is None:
        # An API key is not somebody. Inventing a profile for one would put a
        # person's name on a pipeline's actions.
        raise PlimsollError(
            ErrorCode.PERMISSION_DENIED, "This endpoint describes a user, not an API key."
        )
    profile = await user_repo.get_profile(session, principal.user_id)
    if profile is None:
        raise PlimsollError(ErrorCode.NOT_FOUND, "The account no longer exists.")
    return MeResponse(
        id=str(profile.id),
        email=profile.email,
        name=profile.name,
        org_role=profile.org_role,
        organization_id=str(profile.organization_id),
    )
