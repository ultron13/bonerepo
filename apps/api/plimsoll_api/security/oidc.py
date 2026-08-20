"""The OpenID Connect authorisation-code flow, and what has to be true of an
ID token before anybody is signed in on the strength of it.

Every check here exists because skipping it is a known way in:

* The signature, against the provider's published keys. Without it an ID token
  is a JSON document anybody can write.
* `iss`, against the issuer configured for this organisation. A valid token
  from a different provider is still somebody else's token.
* `aud`, against this deployment's client id. A token minted for another
  application at the same provider would otherwise be accepted here.
* `nonce`, against the value this deployment generated moments earlier. This
  is what stops a token obtained elsewhere being replayed into a sign-in.
* `exp` and `iat`, with a small tolerance for clock drift and no more.
* `email_verified`. An unverified address is a claim about an inbox nobody
  checked, and provisioning on one lets anybody who can register at the
  provider inherit an account by choosing an address.

PKCE is used even though this is a confidential client with a secret. It costs
one hash and removes the authorisation code from the set of things worth
stealing.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

# Long enough that guessing is not a strategy, and URL-safe.
STATE_BYTES = 32
NONCE_BYTES = 32
VERIFIER_BYTES = 48

CLOCK_SKEW_SECONDS = 60
DISCOVERY_TIMEOUT_SECONDS = 10


class OidcError(Exception):
    """The flow cannot continue. The message is safe to show a person."""


@dataclass(frozen=True)
class Discovery:
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    issuer: str


@dataclass(frozen=True)
class Identity:
    subject: str
    email: str
    name: str
    groups: list[str]


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def new_state() -> str:
    return _b64(secrets.token_bytes(STATE_BYTES))


def new_nonce() -> str:
    return _b64(secrets.token_bytes(NONCE_BYTES))


def new_verifier() -> str:
    return _b64(secrets.token_bytes(VERIFIER_BYTES))


def challenge_for(verifier: str) -> str:
    return _b64(hashlib.sha256(verifier.encode()).digest())


async def discover(issuer: str) -> Discovery:
    """Read the provider's own description of itself.

    Configuration names an issuer and nothing else, because every endpoint
    below it can move and a provider that publishes discovery is the only one
    that knows where they are now.
    """
    url = issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT_SECONDS) as http:
            response = await http.get(url)
            response.raise_for_status()
            document = response.json()
    except Exception as exc:
        raise OidcError(f"The identity provider at {issuer} could not be reached.") from exc

    try:
        found = Discovery(
            authorization_endpoint=document["authorization_endpoint"],
            token_endpoint=document["token_endpoint"],
            jwks_uri=document["jwks_uri"],
            issuer=document["issuer"],
        )
    except KeyError as exc:
        raise OidcError(f"The provider's discovery document is missing {exc}.") from exc

    # A discovery document that names a different issuer than the one asked
    # for is either misconfigured or a redirect somewhere unintended, and
    # either way the trust anchor no longer matches what was configured.
    if found.issuer.rstrip("/") != issuer.rstrip("/"):
        raise OidcError(f"The provider at {issuer} identifies itself as {found.issuer}.")
    return found


def authorization_url(
    discovery: Discovery,
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
    nonce: str,
    verifier: str,
) -> str:
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge_for(verifier),
        "code_challenge_method": "S256",
    }
    separator = "&" if "?" in discovery.authorization_endpoint else "?"
    return f"{discovery.authorization_endpoint}{separator}{urlencode(query)}"


async def exchange_code(
    discovery: Discovery,
    *,
    code: str,
    redirect_uri: str,
    client_id: str,
    client_secret: str,
    verifier: str,
) -> str:
    """Trade the authorisation code for an ID token. Returns the raw token."""
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "client_secret": client_secret,
        "code_verifier": verifier,
    }
    try:
        async with httpx.AsyncClient(timeout=DISCOVERY_TIMEOUT_SECONDS) as http:
            response = await http.post(discovery.token_endpoint, data=form)
    except Exception as exc:
        raise OidcError("The identity provider's token endpoint could not be reached.") from exc

    if response.status_code != 200:
        # The provider's own error text is not shown: it can carry the code and
        # the client id, and this message reaches a browser.
        raise OidcError("The identity provider rejected the sign-in.")

    body = response.json()
    token = body.get("id_token")
    if not token:
        raise OidcError("The identity provider returned no ID token.")
    return str(token)


def verify(
    raw_token: str,
    *,
    jwks_uri: str,
    issuer: str,
    client_id: str,
    nonce: str,
    groups_claim: str,
) -> Identity:
    """Everything in this module's docstring, in one place, before a person is
    signed in on the strength of this token."""
    try:
        key = PyJWKClient(jwks_uri).get_signing_key_from_jwt(raw_token)
    except Exception as exc:
        raise OidcError("The identity provider's signing key could not be read.") from exc

    try:
        claims: dict[str, Any] = jwt.decode(
            raw_token,
            key.key,
            algorithms=["RS256", "RS384", "RS512", "ES256", "ES384"],
            audience=client_id,
            issuer=issuer,
            leeway=CLOCK_SKEW_SECONDS,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.InvalidTokenError as exc:
        raise OidcError(f"The identity token is not valid: {exc}") from exc

    if claims.get("nonce") != nonce:
        # The token is genuine and was not minted for this sign-in. Accepting
        # it would let one obtained anywhere else be replayed into this one.
        raise OidcError("The identity token does not belong to this sign-in.")

    email = claims.get("email")
    if not email:
        raise OidcError("The identity provider did not return an email address.")
    if claims.get("email_verified") is not True:
        raise OidcError(
            "The identity provider has not verified this email address. "
            "An account is not created for an address nobody has confirmed."
        )

    raw_groups = claims.get(groups_claim) or []
    groups = [str(group) for group in raw_groups] if isinstance(raw_groups, list) else []

    return Identity(
        subject=str(claims["sub"]),
        email=str(email).lower(),
        name=str(claims.get("name") or claims.get("preferred_username") or email),
        groups=groups,
    )
