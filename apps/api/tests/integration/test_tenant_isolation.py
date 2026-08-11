import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration

OWNER_URL = os.environ.get(
    "PLIMSOLL_TEST_MIGRATION_URL",
    "postgresql+psycopg://plimsoll_owner:plimsoll_owner_dev@localhost:5432/plimsoll",
)
APP_URL = os.environ.get(
    "PLIMSOLL_TEST_APP_URL",
    "postgresql+psycopg://plimsoll_app:plimsoll_app_dev@localhost:5432/plimsoll",
)


@contextmanager
def _owner_connection(org_id: uuid.UUID | None = None) -> Iterator[sa.Connection]:
    """An owner transaction, optionally scoped to an organisation.

    Every tenant table is FORCEd, so the owner is subject to the policies too
    and cannot write a row it has not scoped itself to. That is the point of
    FORCE: there is no role for which the boundary quietly disappears.
    """
    engine = sa.create_engine(OWNER_URL)
    with engine.begin() as connection:
        if org_id is not None:
            connection.execute(
                sa.text("SELECT set_config('app.current_org_id', :org, true)"),
                {"org": str(org_id)},
            )
        yield connection


@pytest.fixture
def two_organisations() -> Iterator[tuple[uuid.UUID, uuid.UUID]]:
    left, right = uuid.uuid4(), uuid.uuid4()
    for org_id, label in ((left, "left"), (right, "right")):
        slug = f"{label}-{org_id.hex[:8]}"
        with _owner_connection(org_id) as connection:
            connection.execute(
                sa.text("INSERT INTO organizations (id, name, slug) VALUES (:id, :name, :slug)"),
                {"id": org_id, "name": slug, "slug": slug},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO projects (id, organization_id, name, project_key) "
                    "VALUES (:id, :org, :name, :key)"
                ),
                {"id": uuid.uuid4(), "org": org_id, "name": slug, "key": slug[:20]},
            )
    yield left, right
    for org_id in (left, right):
        with _owner_connection(org_id) as connection:
            connection.execute(
                sa.text("DELETE FROM projects WHERE organization_id = :org"), {"org": org_id}
            )
            connection.execute(
                sa.text("DELETE FROM organizations WHERE id = :org"), {"org": org_id}
            )


def _projects_visible_to(org_id: uuid.UUID | None) -> list[uuid.UUID]:
    engine = sa.create_engine(APP_URL)
    with engine.begin() as connection:
        if org_id is not None:
            connection.execute(
                sa.text("SELECT set_config('app.current_org_id', :org, true)"),
                {"org": str(org_id)},
            )
        rows = connection.execute(sa.text("SELECT organization_id FROM projects")).scalars().all()
    return list(rows)


def test_an_organisation_sees_only_its_own_rows(
    two_organisations: tuple[uuid.UUID, uuid.UUID],
) -> None:
    left, right = two_organisations
    assert set(_projects_visible_to(left)) == {left}
    assert set(_projects_visible_to(right)) == {right}


def test_without_the_setting_nothing_is_visible(
    two_organisations: tuple[uuid.UUID, uuid.UUID],
) -> None:
    assert _projects_visible_to(None) == []


def test_the_runtime_role_is_not_the_table_owner() -> None:
    engine = sa.create_engine(APP_URL)
    with engine.connect() as connection:
        owner = connection.execute(
            sa.text("SELECT tableowner FROM pg_tables WHERE tablename = 'projects'")
        ).scalar()
        current = connection.execute(sa.text("SELECT current_user")).scalar()
    assert owner != current, "RLS does not apply to a table's owner"


def test_even_the_owner_cannot_write_outside_the_setting() -> None:
    """FORCE, not merely ENABLE. Without it the owner would be exempt and the
    migration and seed paths would silently cross tenants."""
    engine = sa.create_engine(OWNER_URL)
    with pytest.raises(sa.exc.ProgrammingError), engine.begin() as connection:
        connection.execute(
            sa.text("INSERT INTO organizations (id, name, slug) VALUES (:id, :name, :slug)"),
            {"id": uuid.uuid4(), "name": "unscoped", "slug": f"unscoped-{uuid.uuid4().hex[:8]}"},
        )


def test_row_level_security_is_forced_on_every_tenant_table() -> None:
    engine = sa.create_engine(OWNER_URL)
    with engine.connect() as connection:
        rows = (
            connection.execute(
                sa.text(
                    "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r' "
                    "AND c.relrowsecurity AND NOT c.relforcerowsecurity"
                )
            )
            .scalars()
            .all()
        )
    assert list(rows) == [], f"RLS enabled but not forced on: {rows}"


def test_every_tenant_table_has_a_policy() -> None:
    """A table with RLS enabled but no policy denies everything, which looks
    like a bug; a table with neither is an open door. Both are failures."""
    engine = sa.create_engine(OWNER_URL)
    with engine.connect() as connection:
        rows = (
            connection.execute(
                sa.text(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r' "
                    "AND c.relname <> 'alembic_version' "
                    "AND NOT EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid = c.oid)"
                )
            )
            .scalars()
            .all()
        )
    assert list(rows) == [], f"no tenant policy on: {rows}"


def test_auth_lookup_user_resolves_a_user_with_no_organisation_set(
    two_organisations: tuple[uuid.UUID, uuid.UUID],
) -> None:
    """The pre-authentication lookup is the whole point of the function: it has
    to work when app.current_org_id is unset, which is every login."""
    left, _ = two_organisations
    email = f"lookup-{left.hex[:8]}@example.com"
    with _owner_connection(left) as connection:
        connection.execute(
            sa.text(
                "INSERT INTO users (id, organization_id, email, name, org_role) "
                "VALUES (:id, :org, :email, 'Lookup', 'VIEWER')"
            ),
            {"id": uuid.uuid4(), "org": left, "email": email},
        )
    try:
        app_engine = sa.create_engine(APP_URL)
        with app_engine.begin() as connection:
            row = connection.execute(
                sa.text("SELECT organization_id FROM auth_lookup_user(:email)"),
                {"email": email},
            ).first()
        assert row is not None, "login cannot resolve a user before an organisation is known"
        assert row.organization_id == left
    finally:
        with _owner_connection(left) as connection:
            connection.execute(sa.text("DELETE FROM users WHERE email = :email"), {"email": email})


def test_auth_lookup_user_exposes_only_the_login_columns() -> None:
    engine = sa.create_engine(APP_URL)
    with engine.connect() as connection:
        columns = connection.execute(
            sa.text("SELECT proargnames FROM pg_proc WHERE proname = 'auth_lookup_user'")
        ).scalar()
    assert columns is not None, "auth_lookup_user does not exist"
    names = set(columns)
    assert names >= {"id", "organization_id", "password_hash", "status", "org_role"}
    assert "name" not in names
