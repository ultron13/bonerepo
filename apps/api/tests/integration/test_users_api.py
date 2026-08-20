"""Adding and removing the people who use this.

Until now a user existed only if `seed.py` wrote one, so a second person
needed a hand-written INSERT. That makes the product single-user in practice,
and makes offboarding a DELETE somebody runs from memory.
"""

import contextlib
import os
import uuid
from collections.abc import Iterator

import httpx
import pytest
import sqlalchemy as sa

from plimsoll_api.db.session import session_for_org
from plimsoll_api.errors import PlimsollError
from plimsoll_api.repositories import users_admin as repo
from plimsoll_api.services import users as service
from plimsoll_contracts.errors import ErrorCode
from plimsoll_contracts.users import UserInvite
from tests.integration.conftest import ADMIN, API_URL

pytestmark = pytest.mark.integration

OWNER_URL = os.environ.get(
    "PLIMSOLL_TEST_MIGRATION_URL",
    "postgresql+psycopg://plimsoll_owner:plimsoll_owner_dev@localhost:5432/plimsoll",
)


def _scoped(connection: object, org_id: uuid.UUID) -> None:
    connection.execute(  # type: ignore[attr-defined]
        sa.text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(org_id)}
    )


def _make_org_with_one_admin() -> tuple[uuid.UUID, uuid.UUID]:
    org_id, admin_id = uuid.uuid4(), uuid.uuid4()
    engine = sa.create_engine(OWNER_URL)
    with engine.begin() as connection:
        _scoped(connection, org_id)
        connection.execute(
            sa.text("INSERT INTO organizations (id, name, slug) VALUES (:id, :n, :s)"),
            {"id": org_id, "n": "users-test", "s": f"users-{org_id.hex[:8]}"},
        )
        connection.execute(
            sa.text(
                "INSERT INTO users (id, organization_id, email, name, org_role, password_hash) "
                "VALUES (:id, :org, :email, 'Only Admin', 'ORG_ADMIN', 'x')"
            ),
            {"id": admin_id, "org": org_id, "email": f"only-{admin_id.hex[:8]}@example.com"},
        )
    engine.dispose()
    return org_id, admin_id


def _drop_org(org_id: uuid.UUID) -> None:
    engine = sa.create_engine(OWNER_URL)
    with engine.begin() as connection:
        _scoped(connection, org_id)
        for statement in (
            "DELETE FROM audit_logs WHERE organization_id = :org",
            "DELETE FROM refresh_token_history WHERE organization_id = :org",
            "DELETE FROM refresh_token_families WHERE organization_id = :org",
            "DELETE FROM users WHERE organization_id = :org",
        ):
            connection.execute(sa.text(statement), {"org": org_id})
        connection.execute(sa.text("DELETE FROM organizations WHERE id = :org"), {"org": org_id})
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def _clean_up_invited_users() -> Iterator[None]:
    """These run against the shared demo organisation, and one of them promotes
    somebody to administrator. Left behind, that changes what a later test
    sees -- the last-administrator guard in particular stops being reachable.
    """
    yield
    # The organisation is read from the API rather than the database: every
    # tenant table is FORCEd, so a scoped connection is needed to look it up
    # and the scope is the thing being looked up.
    with httpx.Client(base_url=API_URL, timeout=30) as client:
        token = client.post("/api/v1/auth/login", json=ADMIN).json()["accessToken"]
        client.headers["Authorization"] = f"Bearer {token}"
        org_id = uuid.UUID(client.get("/api/v1/auth/me").json()["organizationId"])

    engine = sa.create_engine(OWNER_URL)
    with engine.begin() as connection:
        _scoped(connection, org_id)
        connection.execute(
            sa.text(
                "DELETE FROM refresh_token_history WHERE family_id IN "
                "(SELECT id FROM refresh_token_families WHERE user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'joiner-%@example.com'))"
            )
        )
        connection.execute(
            sa.text(
                "DELETE FROM refresh_token_families WHERE user_id IN "
                "(SELECT id FROM users WHERE email LIKE 'joiner-%@example.com')"
            )
        )
        connection.execute(
            sa.text(
                "DELETE FROM audit_logs WHERE entity_type = 'user' AND entity_id IN "
                "(SELECT id FROM users WHERE email LIKE 'joiner-%@example.com')"
            )
        )
        connection.execute(sa.text("DELETE FROM users WHERE email LIKE 'joiner-%@example.com'"))
    engine.dispose()


def _invite(client: httpx.Client, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "email": f"joiner-{uuid.uuid4().hex[:8]}@example.com",
        "name": "New Joiner",
        "orgRole": "VIEWER",
    }
    body.update(overrides)
    return dict(client.post("/api/v1/users", json=body).json())


@contextlib.contextmanager
def _session_for(email: str, password: str) -> Iterator[httpx.Client]:
    """A real signed-in session, refresh cookie and all -- which is the part
    that matters here, because the cookie is what outlives a deactivation."""
    with httpx.Client(base_url=API_URL, timeout=30) as client:
        response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert response.status_code == 200, response.text
        client.headers["Authorization"] = f"Bearer {response.json()['accessToken']}"
        yield client


def test_an_invited_user_can_sign_in(admin_client: httpx.Client) -> None:
    invited = _invite(admin_client)
    with _session_for(str(invited["email"]), str(invited["temporaryPassword"])) as client:
        me = client.get("/api/v1/auth/me").json()
        assert me["email"] == invited["email"]
        assert me["orgRole"] == "VIEWER"


def test_an_invited_user_gets_only_the_role_they_were_given(admin_client: httpx.Client) -> None:
    """A viewer who could invite users could promote themselves to admin."""
    invited = _invite(admin_client)
    with _session_for(str(invited["email"]), str(invited["temporaryPassword"])) as client:
        response = client.post(
            "/api/v1/users",
            json={"email": "x@example.com", "name": "X", "orgRole": "ORG_ADMIN"},
        )
        assert response.status_code == 403


def test_deactivation_ends_the_session_it_does_not_wait_it_out(admin_client: httpx.Client) -> None:
    """The one that matters.

    An access token is short-lived, so revoking a role is often written as
    "they will lose it within the hour". A refresh token is not: it mints new
    access tokens for fourteen days. If deactivation only stops new sign-ins,
    somebody who was removed keeps working for a fortnight, which is not
    offboarding -- it is a delay.
    """
    invited = _invite(admin_client)
    with _session_for(str(invited["email"]), str(invited["temporaryPassword"])) as client:
        # Prove the premise: the session works, and its refresh token works,
        # before anything is deactivated. Otherwise a broken fixture would
        # look like a passing test.
        assert client.get("/api/v1/auth/me").status_code == 200
        assert client.post("/api/v1/auth/refresh").status_code == 200

        assert admin_client.post(f"/api/v1/users/{invited['id']}/deactivate").status_code == 200

        # The refresh cookie is still in the jar and still unexpired. It must
        # stop working anyway.
        assert client.post("/api/v1/auth/refresh").status_code == 401


def test_a_deactivated_user_cannot_sign_in_again(admin_client: httpx.Client) -> None:
    invited = _invite(admin_client)
    admin_client.post(f"/api/v1/users/{invited['id']}/deactivate")
    with httpx.Client(base_url=API_URL, timeout=30) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"email": invited["email"], "password": invited["temporaryPassword"]},
        )
        assert response.status_code == 401


def test_reactivation_restores_access(admin_client: httpx.Client) -> None:
    invited = _invite(admin_client)
    admin_client.post(f"/api/v1/users/{invited['id']}/deactivate")
    assert admin_client.post(f"/api/v1/users/{invited['id']}/reactivate").status_code == 200
    with _session_for(str(invited["email"]), str(invited["temporaryPassword"])) as client:
        assert client.get("/api/v1/auth/me").status_code == 200


def test_a_promoted_user_gains_the_permissions_of_the_new_role(
    admin_client: httpx.Client,
) -> None:
    invited = _invite(admin_client)
    with _session_for(str(invited["email"]), str(invited["temporaryPassword"])) as client:
        assert client.get("/api/v1/users").status_code == 403

    assert (
        admin_client.patch(
            f"/api/v1/users/{invited['id']}", json={"orgRole": "ORG_ADMIN"}
        ).status_code
        == 200
    )

    # A fresh sign-in, because the role rides on the access token: the point is
    # that the change reached the record, not how fast a live token notices.
    with _session_for(str(invited["email"]), str(invited["temporaryPassword"])) as client:
        assert client.get("/api/v1/auth/me").json()["orgRole"] == "ORG_ADMIN"
        assert client.get("/api/v1/users").status_code == 200


async def test_the_last_administrator_cannot_be_deactivated() -> None:
    """The last administrator is what keeps an organisation reachable.

    In its own organisation, because the assertion is about there being
    exactly one administrator -- and in the shared demo organisation another
    test promoting somebody would quietly make this one vacuous.
    """
    org_id, admin_id = _make_org_with_one_admin()
    try:
        async with session_for_org(org_id) as session:
            with pytest.raises(PlimsollError) as refused:
                await service.deactivate(session, admin_id)
            assert refused.value.code is ErrorCode.VALIDATION_FAILED

            # And the same guard on the other route to the same outcome:
            # demoting the last administrator leaves nobody who can promote.
            with pytest.raises(PlimsollError):
                await service.change_role(session, admin_id, "VIEWER")

        async with session_for_org(org_id) as session:
            still = await repo.get(session, admin_id)
            assert still is not None and still.status == "ACTIVE"
            assert still.org_role == "ORG_ADMIN"
    finally:
        _drop_org(org_id)


async def test_an_administrator_can_be_deactivated_once_another_exists() -> None:
    """The guard is about the last one, not about administrators generally."""
    org_id, admin_id = _make_org_with_one_admin()
    try:
        async with session_for_org(org_id) as session:
            second, _ = await service.invite(
                session,
                org_id,
                UserInvite(
                    email=f"second-{org_id.hex[:8]}@example.com", name="Second", orgRole="ORG_ADMIN"
                ),
            )
            await session.commit()

        async with session_for_org(org_id) as session:
            deactivated = await service.deactivate(session, admin_id)
            assert deactivated.status == "SUSPENDED"
            await session.commit()

        # And now the second one is the last, so it is protected in turn.
        async with session_for_org(org_id) as session:
            with pytest.raises(PlimsollError):
                await service.deactivate(session, second.id)
    finally:
        _drop_org(org_id)


def test_a_viewer_cannot_see_the_directory(viewer_client: httpx.Client) -> None:
    """The list names people and says what each may do."""
    assert viewer_client.get("/api/v1/users").status_code == 403


def test_users_are_listed_within_one_organisation(admin_client: httpx.Client) -> None:
    invited = _invite(admin_client)
    listed = admin_client.get("/api/v1/users").json()["items"]
    assert any(item["id"] == invited["id"] for item in listed)
    assert all("passwordHash" not in item and "temporaryPassword" not in item for item in listed)
