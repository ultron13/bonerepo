import os
import subprocess

import pytest
import sqlalchemy as sa

from plimsoll_api.seed import DEMO_ORG_ID

pytestmark = pytest.mark.integration

OWNER_URL = os.environ.get(
    "PLIMSOLL_TEST_MIGRATION_URL",
    "postgresql+psycopg://plimsoll_owner:plimsoll_owner_dev@localhost:5432/plimsoll",
)

COMPOSE = ["docker", "compose", "-f", "infrastructure/docker/docker-compose.yml"]


def _scalar(query: str) -> object:
    """Every read is scoped to the demo organisation.

    Every tenant table is FORCEd, so even the owner sees nothing without the
    setting. The seed is subject to the same boundary as a request, which is
    why its organisation identifier is deterministic rather than looked up.
    """
    engine = sa.create_engine(OWNER_URL)
    with engine.begin() as connection:
        connection.execute(
            sa.text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(DEMO_ORG_ID)},
        )
        return connection.execute(sa.text(query)).scalar()


def test_demo_organisation_exists() -> None:
    assert _scalar("SELECT count(*) FROM organizations WHERE slug = 'demo'") == 1


def test_both_demo_users_exist_with_the_documented_roles() -> None:
    assert (
        _scalar("SELECT org_role FROM users WHERE email = 'admin@demo.plimsoll.dev'") == "ORG_ADMIN"
    )
    assert (
        _scalar("SELECT org_role FROM users WHERE email = 'viewer@demo.plimsoll.dev'") == "VIEWER"
    )


def test_passwords_are_argon2id_hashed() -> None:
    stored = _scalar("SELECT password_hash FROM users WHERE email = 'admin@demo.plimsoll.dev'")
    assert isinstance(stored, str) and stored.startswith("$argon2id$")


def test_a_demo_project_exists() -> None:
    assert _scalar("SELECT count(*) FROM projects WHERE project_key = 'DEMO'") == 1


def test_target_policy_allows_only_the_demo_target() -> None:
    allowlist = _scalar("SELECT allowlist FROM target_policies WHERE version = 1")
    assert allowlist == ["demo-target"]


def test_seeding_twice_changes_nothing() -> None:
    organisations = _scalar("SELECT count(*) FROM organizations")
    users = _scalar("SELECT count(*) FROM users")
    projects = _scalar("SELECT count(*) FROM projects")
    policies = _scalar("SELECT count(*) FROM target_policies")

    subprocess.run(  # noqa: S603
        [*COMPOSE, "exec", "-T", "api", "uv", "run", "python", "-m", "plimsoll_api.seed"],
        check=True,
        capture_output=True,
    )

    assert _scalar("SELECT count(*) FROM organizations") == organisations
    assert _scalar("SELECT count(*) FROM users") == users
    assert _scalar("SELECT count(*) FROM projects") == projects
    assert _scalar("SELECT count(*) FROM target_policies") == policies
