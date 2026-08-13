import uuid
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from plimsoll_api.db.session import session_for_org

pytestmark = pytest.mark.integration

SECRET = "ghp_thisisnotarealtoken"


def _name() -> str:
    return f"cred-{uuid.uuid4().hex[:8]}"


def _create(client: httpx.Client, name: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/credentials", json={"name": name, "kind": "GIT_TOKEN", "secret": SECRET}
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def _organization(client: httpx.Client) -> uuid.UUID:
    return uuid.UUID(client.get("/api/v1/auth/me").json()["organizationId"])


def test_a_credential_is_created_without_echoing_the_secret(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _name())
    assert SECRET not in str(created)
    assert set(created) == {"id", "name", "kind", "createdAt"}


def test_no_endpoint_returns_the_secret(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _name())
    listed = admin_client.get("/api/v1/credentials")
    assert listed.status_code == 200
    assert SECRET not in listed.text
    # There is no per-credential read: that is where a "reveal" would land.
    assert admin_client.get(f"/api/v1/credentials/{created['id']}").status_code == 405


async def test_the_stored_ciphertext_is_not_the_plaintext(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _name())
    async with session_for_org(_organization(admin_client)) as session:
        stored: bytes = await session.scalar(
            sa.text("SELECT ciphertext FROM credentials WHERE id = :id"),
            {"id": uuid.UUID(str(created["id"]))},
        )
    assert SECRET.encode() not in stored


def test_a_duplicate_name_is_refused(admin_client: httpx.Client) -> None:
    name = _name()
    _create(admin_client, name)
    response = admin_client.post(
        "/api/v1/credentials", json={"name": name, "kind": "GIT_TOKEN", "secret": SECRET}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


def test_an_unused_credential_is_deleted(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _name())
    assert admin_client.delete(f"/api/v1/credentials/{created['id']}").status_code == 204
    remaining = admin_client.get("/api/v1/credentials?limit=200").json()["items"]
    assert all(item["id"] != created["id"] for item in remaining)


def _project(client: httpx.Client) -> dict[str, Any]:
    created: dict[str, Any] = client.post(
        "/api/v1/projects",
        json={"name": "Holder", "projectKey": f"H{uuid.uuid4().hex[:8].upper()}"},
    ).json()
    return created


def _delete_credential(client: httpx.Client, credential_id: str) -> httpx.Response:
    return client.delete(f"/api/v1/credentials/{credential_id}")


async def test_a_credential_in_use_cannot_be_deleted(admin_client: httpx.Client) -> None:
    """The client is synchronous, so its calls live in helpers rather than
    directly in an async test, where they would block the running loop."""
    created = _create(admin_client, _name())
    project = _project(admin_client)

    org = _organization(admin_client)
    async with session_for_org(org) as session:
        await session.execute(
            sa.text(
                "INSERT INTO script_repos "
                "(id, organization_id, project_id, name, repo_url, plan_path, credential_id) "
                "VALUES (:id, :org, :project, 'holder', 'http://example.invalid/r.git', "
                "'perf/plan.jmx', :credential)"
            ),
            {
                "id": uuid.uuid4(),
                "org": org,
                "project": uuid.UUID(str(project["id"])),
                "credential": uuid.UUID(str(created["id"])),
            },
        )

    response = _delete_credential(admin_client, str(created["id"]))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RESOURCE_IN_USE"
    assert response.json()["error"]["details"]["scriptRepos"] == ["holder"]


def test_a_viewer_cannot_create_one(viewer_client: httpx.Client) -> None:
    response = viewer_client.post(
        "/api/v1/credentials", json={"name": _name(), "kind": "GIT_TOKEN", "secret": SECRET}
    )
    assert response.status_code == 403
