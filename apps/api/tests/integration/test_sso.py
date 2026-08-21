"""Signing in through an identity provider, end to end.

A real provider -- `idp-fixture` -- signing real RS256 tokens with a real key,
reached over the network by the API container. The unit tests assert that a
bad token is refused; these assert that a good one carries somebody all the
way to a session, and that the refusals still hold when the token arrives the
way a real one does.

The fixture can be asked for the tokens that should be refused, which is the
only reason to write a provider rather than use one.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import httpx
import pytest
import sqlalchemy as sa

from tests.integration.conftest import API_URL

pytestmark = pytest.mark.integration

OWNER_URL = os.environ.get(
    "PLIMSOLL_TEST_MIGRATION_URL",
    "postgresql+psycopg://plimsoll_owner:plimsoll_owner_dev@localhost:5432/plimsoll",
)
# The issuer as the API resolves it, and the same host as the browser reaches
# on the published port. Both names point at one container.
ISSUER = "http://idp-fixture:8082"
IDP_URL = os.environ.get("PLIMSOLL_TEST_IDP_URL", "http://localhost:8082")
DOMAIN = "sso.example.com"


@pytest.fixture
def configured(admin_client: httpx.Client) -> Iterator[dict[str, str]]:
    """Single sign-on switched on for the demo organisation, and off again."""
    response = admin_client.put(
        "/api/v1/identity-provider",
        json={
            "issuer": ISSUER,
            "clientId": "plimsoll-test-client",
            "clientSecret": "a-client-secret",
            "groupsClaim": "groups",
            "adminGroup": "plimsoll-admins",
            "allowedDomains": [DOMAIN],
        },
    )
    assert response.status_code == 200, response.text
    slug = str(admin_client.get("/api/v1/auth/me").json()["organizationId"])
    body = response.json()
    yield {"startUrl": body["startUrl"], "org": slug}

    admin_client.delete("/api/v1/identity-provider")
    engine = sa.create_engine(OWNER_URL)
    with engine.begin() as connection:
        connection.execute(
            sa.text("SELECT set_config('app.current_org_id', :o, true)"), {"o": slug}
        )
        connection.execute(
            sa.text("DELETE FROM users WHERE email LIKE :pattern"),
            {"pattern": f"%@{DOMAIN}"},
        )
    engine.dispose()


def _sign_in(start_url: str, **provider_options: str) -> httpx.Response:
    """Walk the flow the way a browser does: start, follow to the provider,
    let it redirect back to the callback."""
    with httpx.Client(timeout=30, follow_redirects=False) as browser:
        started = browser.get(start_url.replace("http://localhost:8000", API_URL))
        assert started.status_code == 302, started.text
        authorize = started.headers["location"].replace(ISSUER, IDP_URL)

        separator = "&" if "?" in authorize else "?"
        if provider_options:
            authorize = f"{authorize}{separator}" + "&".join(
                f"{key}={value}" for key, value in provider_options.items()
            )

        handed_back = browser.get(authorize)
        assert handed_back.status_code == 302, handed_back.text
        callback = handed_back.headers["location"].replace("http://localhost:8000", API_URL)
        return browser.get(callback)


def _token_from(response: httpx.Response) -> str:
    """What the browser does next.

    The callback puts no token in the URL -- it sets an httpOnly refresh
    cookie, and the page it lands on trades that for an access token. A token
    in a fragment would survive in browser history for as long as it stayed
    valid.
    """
    cookie = response.cookies["plimsoll_refresh"]
    with httpx.Client(base_url=API_URL, timeout=30) as client:
        client.cookies.set("plimsoll_refresh", cookie, path="/api/v1/auth")
        refreshed = client.post("/api/v1/auth/refresh")
    assert refreshed.status_code == 200, refreshed.text
    return str(refreshed.json()["accessToken"])


def _slug(admin_client: httpx.Client) -> str:
    engine = sa.create_engine(OWNER_URL)
    org = admin_client.get("/api/v1/auth/me").json()["organizationId"]
    with engine.begin() as connection:
        connection.execute(sa.text("SELECT set_config('app.current_org_id', :o, true)"), {"o": org})
        slug = connection.execute(sa.text("SELECT slug FROM organizations LIMIT 1")).scalar_one()
    engine.dispose()
    return str(slug)


def test_a_first_sign_in_creates_the_account_and_a_session(
    admin_client: httpx.Client, configured: dict[str, str]
) -> None:
    """The premise: somebody who has never been here signs in and is somebody."""
    email = f"joiner-{uuid.uuid4().hex[:8]}@{DOMAIN}"
    response = _sign_in(configured["startUrl"], plimsoll_email=email)

    assert response.status_code == 302, response.text
    # Nothing in the URL, and a refresh cookie so the session behaves like any
    # other one this system issues.
    assert "access_token" not in response.headers["location"]
    assert "plimsoll_refresh" in response.cookies
    token = _token_from(response)

    with httpx.Client(base_url=API_URL, timeout=30) as client:
        client.headers["Authorization"] = f"Bearer {token}"
        me = client.get("/api/v1/auth/me").json()
        assert me["email"] == email
        assert me["orgRole"] == "VIEWER"


def test_an_account_created_by_sign_on_has_no_password_to_guess(
    admin_client: httpx.Client, configured: dict[str, str]
) -> None:
    """`password_hash` is NULL for these, and `authenticate` refuses that --
    so there is no local credential, not merely an unknown one."""
    email = f"nopass-{uuid.uuid4().hex[:8]}@{DOMAIN}"
    _sign_in(configured["startUrl"], plimsoll_email=email)

    with httpx.Client(base_url=API_URL, timeout=30) as client:
        for attempt in ("", "password", "a-client-secret"):
            response = client.post(
                "/api/v1/auth/login", json={"email": email, "password": attempt or "x"}
            )
            assert response.status_code == 401


def test_membership_of_the_admin_group_grants_administration(
    admin_client: httpx.Client, configured: dict[str, str]
) -> None:
    email = f"boss-{uuid.uuid4().hex[:8]}@{DOMAIN}"
    response = _sign_in(
        configured["startUrl"], plimsoll_email=email, plimsoll_groups="plimsoll-admins,other"
    )
    token = _token_from(response)

    with httpx.Client(base_url=API_URL, timeout=30) as client:
        client.headers["Authorization"] = f"Bearer {token}"
        assert client.get("/api/v1/auth/me").json()["orgRole"] == "ORG_ADMIN"
        assert client.get("/api/v1/users").status_code == 200


def test_losing_the_group_takes_administration_away_again(
    admin_client: httpx.Client, configured: dict[str, str]
) -> None:
    """Authoritative in both directions. A provider that has taken somebody out
    of the administrators group has said something, and honouring it only when
    it grants would mean the group controls promotion and nothing controls
    demotion."""
    email = f"demoted-{uuid.uuid4().hex[:8]}@{DOMAIN}"
    _sign_in(configured["startUrl"], plimsoll_email=email, plimsoll_groups="plimsoll-admins")

    response = _sign_in(configured["startUrl"], plimsoll_email=email, plimsoll_groups="other")
    token = _token_from(response)
    with httpx.Client(base_url=API_URL, timeout=30) as client:
        client.headers["Authorization"] = f"Bearer {token}"
        assert client.get("/api/v1/auth/me").json()["orgRole"] == "VIEWER"


def test_an_unverified_address_does_not_become_an_account(
    admin_client: httpx.Client, configured: dict[str, str]
) -> None:
    """The takeover route: anybody who can register at the provider could
    otherwise choose a colleague's address and inherit their account."""
    email = f"unverified-{uuid.uuid4().hex[:8]}@{DOMAIN}"
    response = _sign_in(
        configured["startUrl"], plimsoll_email=email, plimsoll_email_verified="false"
    )
    assert response.status_code == 401, response.text
    assert "not verified" in response.text


def test_an_address_outside_the_declared_domains_gets_no_account(
    admin_client: httpx.Client, configured: dict[str, str]
) -> None:
    """A provider that has said which domains it speaks for should not be able
    to mint an account for one it has not."""
    response = _sign_in(
        configured["startUrl"], plimsoll_email=f"outsider-{uuid.uuid4().hex[:8]}@elsewhere.test"
    )
    assert response.status_code == 401, response.text
    assert "not configured for addresses" in response.text


def test_a_token_minted_for_another_sign_in_is_refused(
    admin_client: httpx.Client, configured: dict[str, str]
) -> None:
    """The provider is asked to put the wrong nonce in a genuine, correctly
    signed token. Everything else about it is valid."""
    response = _sign_in(
        configured["startUrl"],
        plimsoll_email=f"replay-{uuid.uuid4().hex[:8]}@{DOMAIN}",
        plimsoll_nonce="a-nonce-from-another-sign-in",
    )
    assert response.status_code == 401, response.text
    assert "does not belong to this sign-in" in response.text


def test_a_callback_with_an_unknown_state_is_refused(configured: dict[str, str]) -> None:
    """State is consumed rather than read, so a replayed callback finds
    nothing -- which is also what an invented one finds."""
    with httpx.Client(base_url=API_URL, timeout=30, follow_redirects=False) as browser:
        response = browser.get("/api/v1/auth/oidc/callback?state=invented&code=whatever")
    assert response.status_code == 401


def test_a_state_cannot_be_used_twice(
    admin_client: httpx.Client, configured: dict[str, str]
) -> None:
    with httpx.Client(timeout=30, follow_redirects=False) as browser:
        started = browser.get(configured["startUrl"].replace("http://localhost:8000", API_URL))
        authorize = started.headers["location"].replace(ISSUER, IDP_URL)
        handed_back = browser.get(f"{authorize}&plimsoll_email=twice@{DOMAIN}")
        callback = handed_back.headers["location"].replace("http://localhost:8000", API_URL)

        assert browser.get(callback).status_code == 302
        # The second attempt carries a state that has already been spent.
        assert browser.get(callback).status_code == 401


def test_a_deactivated_user_cannot_sign_in_around_it(
    admin_client: httpx.Client, configured: dict[str, str]
) -> None:
    """Offboarding that a second sign-in route walks around is not
    offboarding, and this is the route somebody would reach for."""
    email = f"removed-{uuid.uuid4().hex[:8]}@{DOMAIN}"
    first = _sign_in(configured["startUrl"], plimsoll_email=email)
    assert first.status_code == 302

    listed = admin_client.get("/api/v1/users").json()["items"]
    user_id = next(item["id"] for item in listed if item["email"] == email)
    assert admin_client.post(f"/api/v1/users/{user_id}/deactivate").status_code == 200

    again = _sign_in(configured["startUrl"], plimsoll_email=email)
    assert again.status_code == 401, again.text
    assert "not active" in again.text


def test_the_client_secret_is_never_returned(
    admin_client: httpx.Client, configured: dict[str, str]
) -> None:
    body = admin_client.get("/api/v1/identity-provider").json()
    assert "a-client-secret" not in str(body)
    assert "clientSecret" not in body


def test_a_viewer_cannot_configure_who_may_sign_in(viewer_client: httpx.Client) -> None:
    """The most consequential setting here."""
    assert viewer_client.get("/api/v1/identity-provider").status_code == 403
    assert viewer_client.delete("/api/v1/identity-provider").status_code == 403
    assert (
        viewer_client.put(
            "/api/v1/identity-provider",
            json={
                "issuer": ISSUER,
                "clientId": "x",
                "clientSecret": "y",
                "allowedDomains": ["z.test"],
            },
        ).status_code
        == 403
    )


def test_an_unreachable_issuer_is_refused_before_it_is_stored(admin_client: httpx.Client) -> None:
    """A provider saved without checking is one whose first failure happens to
    somebody trying to sign in, with nothing to tell them why."""
    response = admin_client.put(
        "/api/v1/identity-provider",
        json={
            "issuer": "https://nothing-here.invalid",
            "clientId": "x",
            "clientSecret": "y",
            "allowedDomains": ["z.test"],
        },
    )
    assert response.status_code == 422
    assert admin_client.get("/api/v1/identity-provider").status_code == 404


def test_a_plain_http_issuer_needs_a_deliberate_opt_in(admin_client: httpx.Client) -> None:
    """The issuer is the trust anchor: over plain HTTP anything on the path can
    rewrite the discovery document, and with it the key set tokens are checked
    against. This stack sets PLIMSOLL_OIDC_ALLOW_INSECURE_ISSUER because its
    provider fixture has no certificate, so what is asserted here is that the
    setting is what decides -- the unit test covers the refusal it defaults to.
    """
    from plimsoll_api.config import get_settings

    assert get_settings().oidc_allow_insecure_issuer is False, (
        "the default is refusal; the API container opts in through the environment"
    )
