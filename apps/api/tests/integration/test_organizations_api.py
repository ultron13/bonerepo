"""Bringing a tenant into being.

Until this existed, a second organisation needed a hand-written INSERT --
which made a multi-tenant product single-tenant in practice, the same way the
missing user directory made it single-user.

Creating one cannot be authorised from inside one, because there is nobody in
it yet. So it takes an operator's credential, and the tests below are mostly
about that credential being the only way in and being worth as little as
possible if it leaks.
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
TOKEN = os.environ.get("PLIMSOLL_TEST_INSTANCE_TOKEN", "development-only-instance-token")


@pytest.fixture
def created() -> Iterator[list[str]]:
    """Organisations made by a test, removed afterwards."""
    made: list[str] = []
    yield made

    engine = sa.create_engine(OWNER_URL)
    with engine.begin() as connection:
        for org_id in made:
            connection.execute(
                sa.text("SELECT set_config('app.current_org_id', :o, true)"), {"o": org_id}
            )
            for statement in (
                "DELETE FROM audit_logs WHERE organization_id = :o",
                "DELETE FROM refresh_token_history WHERE organization_id = :o",
                "DELETE FROM refresh_token_families WHERE organization_id = :o",
                "DELETE FROM users WHERE organization_id = :o",
            ):
                connection.execute(sa.text(statement), {"o": org_id})
            connection.execute(sa.text("DELETE FROM organizations WHERE id = :o"), {"o": org_id})
    engine.dispose()


def _create(token: str | None = TOKEN, **overrides: object) -> httpx.Response:
    body: dict[str, object] = {
        "name": "Acme Performance",
        "slug": f"acme-{uuid.uuid4().hex[:8]}",
        "adminEmail": f"boss-{uuid.uuid4().hex[:8]}@acme.example.com",
        "adminName": "Ada Boss",
    }
    body.update(overrides)
    headers = {"authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(base_url=API_URL, timeout=30) as client:
        return client.post("/api/v1/organizations", json=body, headers=headers)


def test_a_new_organisation_comes_with_somebody_who_can_use_it(created: list[str]) -> None:
    """The premise, and the reason the two are made together: an organisation
    nobody can enter is only reachable by making somebody, and making somebody
    is the same privilege as this."""
    response = _create()
    assert response.status_code == 201, response.text
    body = response.json()
    created.append(body["id"])

    with httpx.Client(base_url=API_URL, timeout=30) as client:
        signed_in = client.post(
            "/api/v1/auth/login",
            json={"email": body["adminEmail"], "password": body["adminTemporaryPassword"]},
        )
        assert signed_in.status_code == 200, signed_in.text
        client.headers["Authorization"] = f"Bearer {signed_in.json()['accessToken']}"

        me = client.get("/api/v1/auth/me").json()
        assert me["organizationId"] == body["id"]
        assert me["orgRole"] == "ORG_ADMIN"
        # And can immediately do the thing an administrator is for.
        assert client.get("/api/v1/users").status_code == 200


def test_a_new_organisation_starts_empty(created: list[str]) -> None:
    """A tenant that could see anything already there would be the worst
    possible first impression, and the worst possible bug."""
    body = _create().json()
    created.append(body["id"])

    with httpx.Client(base_url=API_URL, timeout=30) as client:
        token = client.post(
            "/api/v1/auth/login",
            json={"email": body["adminEmail"], "password": body["adminTemporaryPassword"]},
        ).json()["accessToken"]
        client.headers["Authorization"] = f"Bearer {token}"

        assert client.get("/api/v1/projects?limit=50").json()["items"] == []
        assert client.get("/api/v1/runs?limit=50").json()["items"] == []
        assert client.get("/api/v1/generator-pools?limit=50").json()["items"] == []
        # One person: the administrator just created, and nobody else's.
        assert len(client.get("/api/v1/users").json()["items"]) == 1


@pytest.mark.parametrize(
    "token",
    [
        None,
        "",
        "not-the-token",
        # A prefix and an extension of the real value. Neither should open
        # anything, and a comparison that stopped at the first difference
        # would still refuse both -- what it would leak is how far it got,
        # which is why the check is constant time.
        "development-only-instance-toke",
        "development-only-instance-tokenX",
    ],
)
def test_only_the_instance_token_opens_this(token: str | None) -> None:
    assert _create(token=token).status_code in (401, 403)


def test_a_users_own_token_will_not_do(admin_client: httpx.Client) -> None:
    """An organisation administrator is the most powerful principal this
    product issues, and creating tenants is still not theirs to do."""
    presented = admin_client.headers["Authorization"].removeprefix("Bearer ")
    assert _create(token=presented).status_code == 401


def test_a_slug_is_not_taken_twice(created: list[str]) -> None:
    """The slug is what somebody types to reach their identity provider, so
    two organisations sharing one would send people to the wrong sign-in."""
    first = _create().json()
    created.append(first["id"])

    clash = _create(slug=first["slug"])
    assert clash.status_code == 409, clash.text


@pytest.mark.parametrize("slug", ["Acme Corp", "-leading", "trailing-", "under_score", ""])
def test_a_slug_has_to_survive_a_url(slug: str) -> None:
    assert _create(slug=slug).status_code == 422


def test_the_slug_check_sees_organisations_it_cannot_read(created: list[str]) -> None:
    """The reason that check is a SECURITY DEFINER function.

    Creation runs inside a session scoped to the organisation being created,
    and `organizations` is FORCEd on its own id -- so a direct read for an
    existing slug is filtered to nothing and would always answer "free". The
    duplicate would then be refused by the unique constraint, as an internal
    error rather than a conflict.
    """
    clash = _create(slug="demo")
    assert clash.status_code == 409, clash.text
    assert "demo" in clash.text
