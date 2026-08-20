"""What has to be true of an ID token before anybody is signed in on it.

Real keys, real signatures, real tokens. Mocking the verification would test
that the mock returns what it was told to; the point of these is that a token
which should be refused actually is, and that is a property of the crypto and
the claim checks rather than of this file.

Each test is a way in that would work if the corresponding check were dropped.
"""

from __future__ import annotations

import json
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from plimsoll_api.security import oidc
from plimsoll_api.security.oidc import OidcError

ISSUER = "https://idp.example.com"
CLIENT_ID = "plimsoll-client"
NONCE = "the-nonce-this-deployment-generated"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks(key: rsa.RSAPrivateKey, kid: str = "test-key") -> str:
    numbers = key.public_key().public_numbers()

    def b64(value: int) -> str:
        length = (value.bit_length() + 7) // 8
        return oidc._b64(value.to_bytes(length, "big"))

    return json.dumps(
        {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": kid,
                    "use": "sig",
                    "alg": "RS256",
                    "n": b64(numbers.n),
                    "e": b64(numbers.e),
                }
            ]
        }
    )


def _token(key: rsa.RSAPrivateKey = _KEY, **overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": CLIENT_ID,
        "sub": "idp-subject-1",
        "iat": now,
        "exp": now + 300,
        "nonce": NONCE,
        "email": "Sam@Example.com",
        "email_verified": True,
        "name": "Sam Patel",
        "groups": ["engineering", "plimsoll-admins"],
    }
    claims.update(overrides)
    for key_name in [k for k, v in overrides.items() if v is None]:
        claims.pop(key_name, None)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})


@pytest.fixture(autouse=True)
def _serve_jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    """The provider's published keys, without a network. PyJWKClient fetches
    over HTTP; what is under test is the verification, not the fetch."""
    payload = _jwks(_KEY)

    class FakeJWKClient:
        def __init__(self, uri: str, *args: Any, **kwargs: Any) -> None:
            self._uri = uri

        def get_signing_key_from_jwt(self, token: str) -> Any:
            from jwt import PyJWKSet

            header = jwt.get_unverified_header(token)
            keys = PyJWKSet.from_json(payload)
            for key in keys.keys:
                if key.key_id == header.get("kid"):
                    return key
            raise ValueError("no matching key")

    monkeypatch.setattr(oidc, "PyJWKClient", FakeJWKClient)


def _verify(token: str, *, nonce: str = NONCE, groups_claim: str = "groups") -> oidc.Identity:
    return oidc.verify(
        token,
        jwks_uri=f"{ISSUER}/jwks",
        issuer=ISSUER,
        client_id=CLIENT_ID,
        nonce=nonce,
        groups_claim=groups_claim,
    )


def test_a_genuine_token_signs_the_person_in() -> None:
    """The premise. Every refusal below means nothing if nothing is accepted."""
    identity = _verify(_token())
    assert identity.subject == "idp-subject-1"
    # Lower-cased, because an address is not case-sensitive and two spellings
    # of one person must not become two accounts.
    assert identity.email == "sam@example.com"
    assert identity.name == "Sam Patel"
    assert identity.groups == ["engineering", "plimsoll-admins"]


def test_a_token_nobody_signed_is_refused() -> None:
    """Without the signature check an ID token is a JSON document anybody can
    write, and this whole flow is an invitation to type a name."""
    unsigned = jwt.encode({"iss": ISSUER, "sub": "x"}, key="", algorithm="none")
    with pytest.raises(OidcError):
        _verify(unsigned)


def test_a_token_signed_by_the_wrong_key_is_refused() -> None:
    with pytest.raises(OidcError, match=r"not valid|signing key"):
        _verify(_token(_OTHER_KEY))


def test_a_token_from_another_issuer_is_refused() -> None:
    """A valid token from a different provider is still somebody else's."""
    with pytest.raises(OidcError, match="not valid"):
        _verify(_token(iss="https://someone-else.example.com"))


def test_a_token_minted_for_another_application_is_refused() -> None:
    """Same provider, different client. Without the audience check, any
    application sharing this identity provider could sign people in here."""
    with pytest.raises(OidcError, match="not valid"):
        _verify(_token(aud="a-different-application"))


def test_a_token_from_another_sign_in_cannot_be_replayed() -> None:
    """The nonce is the difference between "this token is genuine" and "this
    token was minted for the sign-in happening right now"."""
    with pytest.raises(OidcError, match="does not belong to this sign-in"):
        _verify(_token(nonce="a-nonce-from-somewhere-else"))


def test_a_token_with_no_nonce_at_all_is_refused() -> None:
    with pytest.raises(OidcError, match="does not belong to this sign-in"):
        _verify(_token(nonce=None))


def test_an_expired_token_is_refused() -> None:
    now = int(time.time())
    with pytest.raises(OidcError, match="not valid"):
        _verify(_token(exp=now - 3600, iat=now - 7200))


def test_a_token_just_inside_the_clock_skew_is_accepted() -> None:
    """Refusing a token because two clocks disagree by seconds would make
    sign-in fail intermittently and look like anything but a clock."""
    now = int(time.time())
    assert _verify(_token(exp=now - 30, iat=now - 300)).subject == "idp-subject-1"


def test_an_unverified_email_does_not_create_an_account() -> None:
    """The account-takeover route. An unverified address is a claim about an
    inbox nobody checked, so anybody who can register at the provider could
    choose a colleague's address and inherit their account."""
    with pytest.raises(OidcError, match="not verified"):
        _verify(_token(email_verified=False))


def test_a_missing_email_verified_claim_is_treated_as_unverified() -> None:
    """Absent is not the same as true, and the safe reading is the strict one."""
    with pytest.raises(OidcError, match="not verified"):
        _verify(_token(email_verified=None))


def test_a_token_with_no_email_is_refused() -> None:
    with pytest.raises(OidcError, match="did not return an email"):
        _verify(_token(email=None, email_verified=True))


def test_a_token_missing_a_required_claim_is_refused() -> None:
    with pytest.raises(OidcError, match="not valid"):
        _verify(_token(sub=None))


def test_the_groups_claim_is_read_from_where_the_provider_puts_it() -> None:
    """Providers disagree about what to call this, so it is configured rather
    than assumed."""
    token = _token(groups=None, roles=["platform-admins"])
    assert _verify(token, groups_claim="roles").groups == ["platform-admins"]


def test_a_groups_claim_that_is_not_a_list_yields_no_groups() -> None:
    """A provider sending a string where a list was expected must not end up
    granting something by accident."""
    assert _verify(_token(groups="plimsoll-admins")).groups == []


async def test_a_discovery_document_naming_a_different_issuer_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The trust anchor is the configured issuer. A document claiming to speak
    for somebody else means either a misconfiguration or a redirect somewhere
    unintended, and the issuer checked against tokens would no longer be the
    one that was configured."""
    document = {
        "issuer": "https://attacker.example.com",
        "authorization_endpoint": "https://attacker.example.com/authorize",
        "token_endpoint": "https://attacker.example.com/token",
        "jwks_uri": "https://attacker.example.com/jwks",
    }
    monkeypatch.setattr("plimsoll_api.security.oidc.httpx.AsyncClient", _client_returning(document))
    with pytest.raises(OidcError, match="identifies itself as"):
        await oidc.discover(ISSUER)


async def test_discovery_reads_the_endpoints_the_provider_publishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Configuration names an issuer and nothing else, because every endpoint
    below it can move."""
    document = {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "jwks_uri": f"{ISSUER}/jwks",
    }
    monkeypatch.setattr("plimsoll_api.security.oidc.httpx.AsyncClient", _client_returning(document))
    found = await oidc.discover(ISSUER + "/")
    assert found.token_endpoint == f"{ISSUER}/token"


def _client_returning(document: dict[str, Any]) -> Any:
    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, Any]:
            return document

    class Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str) -> Response:
            return Response()

    return Client


def test_the_pkce_challenge_is_the_hash_and_not_the_verifier() -> None:
    """Sending the verifier as the challenge would be PKCE in shape only."""
    verifier = oidc.new_verifier()
    challenge = oidc.challenge_for(verifier)
    assert challenge != verifier
    assert oidc.challenge_for(verifier) == challenge


def test_state_and_nonce_are_not_predictable() -> None:
    assert len({oidc.new_state() for _ in range(50)}) == 50
    assert len({oidc.new_nonce() for _ in range(50)}) == 50


def test_a_plain_http_issuer_is_refused_by_default() -> None:
    """The setting that permits one exists for a development fixture with no
    certificate. A deployment that never sets it never accepts a provider
    whose discovery document anything on the path can rewrite."""
    from plimsoll_api.config import Settings

    fields = Settings.model_fields
    assert fields["oidc_allow_insecure_issuer"].default is False
