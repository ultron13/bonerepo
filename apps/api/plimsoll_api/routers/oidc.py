"""Single sign-on: starting a flow, and finishing one.

Two endpoints, both unauthenticated by necessity -- signing in is what they
are for. What confines them is that neither takes anything from the caller that
decides who they become. The organisation comes from the slug in the path, the
identity comes from a signed token, and everything in between is looked up
from state this deployment wrote itself.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Query, Response
from fastapi.responses import RedirectResponse

from plimsoll_api.config import get_settings
from plimsoll_api.db.session import session_for_org
from plimsoll_api.dependencies import AnonymousSession
from plimsoll_api.errors import PlimsollError
from plimsoll_api.messaging import get_bus
from plimsoll_api.repositories import identity_providers as repo
from plimsoll_api.security import oidc
from plimsoll_api.security.oidc import OidcError
from plimsoll_api.security.secrets import get_key_provider
from plimsoll_api.services import audit
from plimsoll_api.services import oidc as service
from plimsoll_api.services.refresh import issue_family
from plimsoll_contracts.errors import ErrorCode

router = APIRouter(prefix="/api/v1/auth/oidc", tags=["auth"])

# Long enough for somebody to read a consent screen and find their phone;
# short enough that an abandoned flow is not a credential lying around.
FLOW_TTL_SECONDS = 600
FLOW_PREFIX = "oidc:flow:"


def _redirect_uri() -> str:
    return get_settings().public_api_url.rstrip("/") + "/api/v1/auth/oidc/callback"


@router.get("/{slug}/start")
async def start(slug: str, session: AnonymousSession) -> RedirectResponse:
    """Send somebody to their organisation's identity provider.

    The slug is the only thing the caller supplies, and all it can do is choose
    which provider they are sent to -- it cannot make them anybody. A slug that
    names no provider is refused the same way a wrong one is, so this is not a
    way to enumerate which organisations use single sign-on.
    """
    provider = await repo.lookup_by_slug(session, slug)
    if provider is None:
        raise PlimsollError(
            ErrorCode.NOT_FOUND, "No single sign-on is configured for that organisation."
        )

    try:
        discovery = await oidc.discover(provider.issuer)
    except OidcError as exc:
        raise PlimsollError(ErrorCode.INTERNAL, str(exc)) from exc

    state, nonce, verifier = oidc.new_state(), oidc.new_nonce(), oidc.new_verifier()
    # Server-side, keyed by a value the browser only ever echoes back. Putting
    # the nonce and the verifier in a cookie would hand both to whoever can
    # read one.
    await get_bus().client.set(
        FLOW_PREFIX + state,
        json.dumps(
            {
                "organizationId": str(provider.organization_id),
                "providerId": str(provider.id),
                "nonce": nonce,
                "verifier": verifier,
            }
        ),
        ex=FLOW_TTL_SECONDS,
    )

    return RedirectResponse(
        oidc.authorization_url(
            discovery,
            client_id=provider.client_id,
            redirect_uri=_redirect_uri(),
            state=state,
            nonce=nonce,
            verifier=verifier,
        ),
        status_code=302,
    )


@router.get("/callback")
async def callback(
    response: Response,
    state: str = Query(...),
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
) -> RedirectResponse:
    """Finish the flow and issue this deployment's own tokens.

    The provider's answer is never trusted further than the ID token it is
    signed into. Nothing from the query string decides who the caller becomes.
    """
    settings = get_settings()
    if error:
        # The provider's own error text is not echoed onward: it reaches a
        # browser, and it can carry identifiers from the request.
        raise PlimsollError(ErrorCode.UNAUTHENTICATED, "The identity provider refused the sign-in.")
    if not code:
        raise PlimsollError(ErrorCode.UNAUTHENTICATED, "The identity provider returned no code.")

    # Consumed, not read: a state that can be replayed is a code that can be
    # replayed with it.
    raw = await get_bus().client.getdel(FLOW_PREFIX + state)
    if raw is None:
        raise PlimsollError(
            ErrorCode.UNAUTHENTICATED,
            "This sign-in has expired or was already completed. Please start again.",
        )
    flow: dict[str, Any] = json.loads(raw)
    org_id = uuid.UUID(flow["organizationId"])

    async with session_for_org(org_id) as session:
        provider = await repo.get_for_org(session)
        if provider is None or not provider.enabled:
            raise PlimsollError(
                ErrorCode.UNAUTHENTICATED, "Single sign-on is no longer configured."
            )
        secret = get_key_provider().decrypt(
            bytes(provider.client_secret_ciphertext), provider.client_secret_key_ref
        )

        try:
            discovery = await oidc.discover(provider.issuer)
            raw_token = await oidc.exchange_code(
                discovery,
                code=code,
                redirect_uri=_redirect_uri(),
                client_id=provider.client_id,
                client_secret=secret.decode(),
                verifier=flow["verifier"],
            )
            identity = oidc.verify(
                raw_token,
                jwks_uri=discovery.jwks_uri,
                issuer=provider.issuer,
                client_id=provider.client_id,
                nonce=flow["nonce"],
                groups_claim=provider.groups_claim,
            )
            # The role is reconciled with the provider inside sign_in and read
            # back from the record when the refresh cookie is traded for an
            # access token, so it is not carried through this response.
            user_id, _role = await service.sign_in(
                session, org_id=org_id, identity=identity, provider=provider
            )
        except OidcError as exc:
            raise PlimsollError(ErrorCode.UNAUTHENTICATED, str(exc)) from exc

        await audit.record_for_user(
            session,
            org_id=org_id,
            user_id=user_id,
            action="auth.sso_sign_in",
            entity_type="user",
            entity_id=user_id,
            metadata={"issuer": provider.issuer, "subject": identity.subject},
        )
        refresh = await issue_family(session, user_id, org_id)

    # No token in the URL. The refresh cookie set below is what the browser
    # carries back, and the page it lands on trades that for an access token
    # over a request nobody else sees. A token in a fragment would survive in
    # browser history and in anything that logs a location, for the whole
    # fifteen minutes it stays valid.
    redirect = RedirectResponse(
        settings.public_web_url.rstrip("/") + "/sign-in/complete", status_code=302
    )
    redirect.set_cookie(
        "plimsoll_refresh",
        refresh,
        httponly=True,
        samesite="lax",
        secure=settings.environment != "development",
        max_age=settings.refresh_token_ttl_seconds,
        path="/api/v1/auth",
    )
    return redirect
