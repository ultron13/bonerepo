import os
import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa

from plimsoll_api.db.session import session_for_org
from plimsoll_api.services.refresh import RefreshRejected, issue_family, rotate

pytestmark = pytest.mark.integration

OWNER_URL = os.environ.get(
    "PLIMSOLL_TEST_MIGRATION_URL",
    "postgresql+psycopg://plimsoll_owner:plimsoll_owner_dev@localhost:5432/plimsoll",
)


@pytest.fixture
def user() -> Iterator[tuple[uuid.UUID, uuid.UUID]]:
    """Fixture rows are inserted through the owner connection, scoped to the
    organisation being created. Every tenant table is FORCEd, so even the owner
    must declare which organisation it is writing as."""
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    engine = sa.create_engine(OWNER_URL)
    with engine.begin() as connection:
        connection.execute(
            sa.text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(org_id)},
        )
        connection.execute(
            sa.text("INSERT INTO organizations (id, name, slug) VALUES (:id, :n, :s)"),
            {"id": org_id, "n": "refresh-test", "s": f"refresh-{org_id.hex[:8]}"},
        )
        connection.execute(
            sa.text(
                "INSERT INTO users (id, organization_id, email, name, org_role) "
                "VALUES (:id, :org, :email, 'T', 'VIEWER')"
            ),
            {"id": user_id, "org": org_id, "email": f"{user_id.hex[:8]}@example.com"},
        )
    yield user_id, org_id
    with engine.begin() as connection:
        connection.execute(
            sa.text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(org_id)},
        )
        connection.execute(
            sa.text("DELETE FROM refresh_token_history WHERE organization_id = :org"),
            {"org": org_id},
        )
        connection.execute(
            sa.text("DELETE FROM refresh_token_families WHERE organization_id = :org"),
            {"org": org_id},
        )
        connection.execute(sa.text("DELETE FROM users WHERE id = :id"), {"id": user_id})
        connection.execute(sa.text("DELETE FROM organizations WHERE id = :org"), {"org": org_id})


async def test_rotation_returns_a_new_token(user: tuple[uuid.UUID, uuid.UUID]) -> None:
    user_id, org_id = user
    async with session_for_org(org_id) as session:
        first = await issue_family(session, user_id, org_id)
        second, returned_user, returned_org = await rotate(session, first)
    assert second != first
    assert returned_user == user_id
    assert returned_org == org_id


async def test_reusing_a_consumed_token_revokes_the_family(
    user: tuple[uuid.UUID, uuid.UUID],
) -> None:
    user_id, org_id = user
    async with session_for_org(org_id) as session:
        first = await issue_family(session, user_id, org_id)
        second, _, _ = await rotate(session, first)

    async with session_for_org(org_id) as session:
        with pytest.raises(RefreshRejected):
            await rotate(session, first)

    # The whole family is dead, so the token that was valid a moment ago fails too.
    async with session_for_org(org_id) as session:
        with pytest.raises(RefreshRejected):
            await rotate(session, second)


async def test_an_unknown_token_is_rejected(user: tuple[uuid.UUID, uuid.UUID]) -> None:
    _, org_id = user
    async with session_for_org(org_id) as session:
        with pytest.raises(RefreshRejected):
            await rotate(session, "not-a-real-token")


async def test_a_rotated_token_can_be_rotated_again(
    user: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """The happy path is a chain, not a single hop: an unrevoked family keeps
    issuing as long as the caller always presents the newest token."""
    user_id, org_id = user
    async with session_for_org(org_id) as session:
        token = await issue_family(session, user_id, org_id)
        for _ in range(3):
            token, _, _ = await rotate(session, token)
    assert token
