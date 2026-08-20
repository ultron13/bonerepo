"""One organisation's data, asked for over HTTP by another one's administrator.

`test_tenant_isolation` proves row-level security is configured: forced on
every tenant table, a policy on each, and the owner unable to write outside the
setting. That is the foundation and it is not the whole claim. RLS can be
perfectly configured and the API still leak, because what confines a request is
the organisation the session was bound to -- and binding it is application code.
A route that forgets, or one that takes the organisation from the request,
would pass every test in that file.

So this asks the question from outside: a real second organisation, a real
signed-in administrator, and every list endpoint there is.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import httpx
import pytest
import sqlalchemy as sa

from plimsoll_api.security.passwords import hash_password
from tests.integration.conftest import API_URL

pytestmark = pytest.mark.integration

OWNER_URL = os.environ.get(
    "PLIMSOLL_TEST_MIGRATION_URL",
    "postgresql+psycopg://plimsoll_owner:plimsoll_owner_dev@localhost:5432/plimsoll",
)
PASSWORD = "a-second-organisation-password"


@pytest.fixture(scope="module")
def neighbour() -> Iterator[dict[str, str]]:
    """A whole other tenant: its own organisation, administrator, project,
    pool, test and run. Built through the owner connection because there is no
    product surface for creating an organisation yet -- which is itself on the
    list, and does not stop this being the thing worth checking."""
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    project_id, pool_id = uuid.uuid4(), uuid.uuid4()
    test_id, run_id = uuid.uuid4(), uuid.uuid4()
    email = f"neighbour-{org_id.hex[:8]}@example.com"

    engine = sa.create_engine(OWNER_URL)
    with engine.begin() as connection:
        connection.execute(
            sa.text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(org_id)}
        )
        connection.execute(
            sa.text("INSERT INTO organizations (id, name, slug) VALUES (:id, :n, :s)"),
            {"id": org_id, "n": "Neighbour", "s": f"neighbour-{org_id.hex[:8]}"},
        )
        connection.execute(
            sa.text(
                "INSERT INTO users (id, organization_id, email, name, org_role, password_hash) "
                "VALUES (:id, :org, :email, 'Neighbour Admin', 'ORG_ADMIN', :hash)"
            ),
            {"id": user_id, "org": org_id, "email": email, "hash": hash_password(PASSWORD)},
        )
        connection.execute(
            sa.text(
                "INSERT INTO projects (id, organization_id, name, project_key, created_by) "
                "VALUES (:id, :org, 'Neighbour Project', :key, :by)"
            ),
            {"id": project_id, "org": org_id, "key": f"N{org_id.hex[:5].upper()}", "by": user_id},
        )
        connection.execute(
            sa.text(
                "INSERT INTO generator_pools "
                "(id, organization_id, name, runtime, config, max_generators, "
                " max_vus_per_generator, status) "
                "VALUES (:id, :org, 'Neighbour Pool', 'docker', "
                " '{\"image\": \"neighbour/generator:1\"}'::jsonb, 2, 100, 'ACTIVE')"
            ),
            {"id": pool_id, "org": org_id},
        )
        connection.execute(
            sa.text(
                "INSERT INTO performance_tests "
                "(id, organization_id, project_id, name, configuration, created_by) "
                "VALUES (:id, :org, :project, 'Neighbour Test', '{}'::jsonb, :by)"
            ),
            {"id": test_id, "org": org_id, "project": project_id, "by": user_id},
        )
        connection.execute(
            sa.text(
                "INSERT INTO test_runs "
                "(id, organization_id, project_id, performance_test_id, run_number, status, "
                " configuration_snapshot, trigger_source, initiated_by) "
                "VALUES (:id, :org, :project, :test, 1, 'COMPLETED', "
                " '{\"workload\": {}}'::jsonb, 'API', :by)"
            ),
            {"id": run_id, "org": org_id, "project": project_id, "test": test_id, "by": user_id},
        )
    engine.dispose()

    yield {
        "org": str(org_id),
        "email": email,
        "project": str(project_id),
        "pool": str(pool_id),
        "test": str(test_id),
        "run": str(run_id),
        "user": str(user_id),
    }

    engine = sa.create_engine(OWNER_URL)
    with engine.begin() as connection:
        connection.execute(
            sa.text("SELECT set_config('app.current_org_id', :org, true)"), {"org": str(org_id)}
        )
        for statement in (
            "DELETE FROM audit_logs WHERE organization_id = :org",
            "DELETE FROM test_runs WHERE organization_id = :org",
            "DELETE FROM performance_tests WHERE organization_id = :org",
            "DELETE FROM generator_pools WHERE organization_id = :org",
            "DELETE FROM projects WHERE organization_id = :org",
            "DELETE FROM refresh_token_history WHERE organization_id = :org",
            "DELETE FROM refresh_token_families WHERE organization_id = :org",
            "DELETE FROM users WHERE organization_id = :org",
        ):
            connection.execute(sa.text(statement), {"org": org_id})
        connection.execute(sa.text("DELETE FROM organizations WHERE id = :org"), {"org": org_id})
    engine.dispose()


def test_the_neighbour_is_real(neighbour: dict[str, str]) -> None:
    """The premise, asserted rather than assumed. Every test below is about
    something being absent, and absence proves nothing if it was never there."""
    with httpx.Client(base_url=API_URL, timeout=30) as client:
        token = client.post(
            "/api/v1/auth/login", json={"email": neighbour["email"], "password": PASSWORD}
        ).json()["accessToken"]
        client.headers["Authorization"] = f"Bearer {token}"

        assert client.get("/api/v1/auth/me").json()["organizationId"] == neighbour["org"]
        assert any(
            item["id"] == neighbour["project"]
            for item in client.get("/api/v1/projects?limit=100").json()["items"]
        )
        assert any(
            item["id"] == neighbour["run"]
            for item in client.get("/api/v1/runs?limit=100").json()["items"]
        )


@pytest.mark.parametrize(
    ("path", "key"),
    [
        ("/api/v1/projects?limit=200", "project"),
        ("/api/v1/runs?limit=200", "run"),
        ("/api/v1/generator-pools?limit=200", "pool"),
        ("/api/v1/users", "user"),
    ],
)
def test_a_listing_never_reaches_across_organisations(
    admin_client: httpx.Client, neighbour: dict[str, str], path: str, key: str
) -> None:
    """The org-wide runs listing is the one to watch: it has no WHERE clause on
    organization_id at all, by design -- row-level security is the boundary and
    an application filter would imply otherwise. That design is only safe if
    the session is bound correctly, which is what this checks."""
    items = admin_client.get(path).json()["items"]
    assert all(item["id"] != neighbour[key] for item in items)


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/projects/{project}",
        "/api/v1/runs/{run}",
        "/api/v1/generator-pools/{pool}",
        "/api/v1/tests/{test}",
        "/api/v1/runs/{run}/status",
        "/api/v1/runs/{run}/metrics",
        "/api/v1/runs/{run}/artifacts",
    ],
)
def test_naming_another_organisations_row_is_a_miss_not_a_read(
    admin_client: httpx.Client, neighbour: dict[str, str], path: str
) -> None:
    """Knowing an identifier must not be worth anything.

    404 rather than 403: telling somebody the row exists but is not theirs
    confirms the identifier, which is the thing they were guessing at. The
    answer is the same one a made-up identifier gets.
    """
    response = admin_client.get(path.format(**neighbour))
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/projects/{project}/tests",
        "/api/v1/projects/{project}/runs",
        "/api/v1/projects/{project}/script-repos",
    ],
)
def test_a_collection_under_another_organisations_parent_is_empty(
    admin_client: httpx.Client, neighbour: dict[str, str], path: str
) -> None:
    """Empty rather than 404, and that is the more careful answer: it is the
    same thing a project with no tests returns, so it does not confirm that
    the parent exists either."""
    response = admin_client.get(path.format(**neighbour))
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []


def test_another_organisations_run_cannot_be_commanded(
    admin_client: httpx.Client, neighbour: dict[str, str]
) -> None:
    """Reading is not the only way to reach across. Stopping somebody else's
    run would be a denial of service with a valid token."""
    for command in ("stop", "cancel"):
        response = admin_client.post(f"/api/v1/runs/{neighbour['run']}/{command}")
        assert response.status_code == 404, f"{command}: {response.text}"


def test_a_deactivation_cannot_reach_another_organisations_administrator(
    admin_client: httpx.Client, neighbour: dict[str, str]
) -> None:
    """The user directory is new, and it is the surface where reaching across
    would be worst: one organisation's administrator ending another's."""
    response = admin_client.post(f"/api/v1/users/{neighbour['user']}/deactivate")
    assert response.status_code == 404, response.text

    engine = sa.create_engine(OWNER_URL)
    with engine.begin() as connection:
        connection.execute(
            sa.text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": neighbour["org"]},
        )
        status = connection.execute(
            sa.text("SELECT status FROM users WHERE id = :id"), {"id": neighbour["user"]}
        ).scalar_one()
    engine.dispose()
    assert status == "ACTIVE"
