import uuid
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from plimsoll_api.db.session import session_for_org

pytestmark = pytest.mark.integration


def _unique_key() -> str:
    return f"K{uuid.uuid4().hex[:8].upper()}"


def _create(client: httpx.Client, key: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/projects",
        json={"name": f"Project {key}", "projectKey": key, "environment": "staging"},
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def _organization(client: httpx.Client) -> uuid.UUID:
    return uuid.UUID(client.get("/api/v1/auth/me").json()["organizationId"])


def test_a_project_is_created_and_read_back(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _unique_key())
    fetched = admin_client.get(f"/api/v1/projects/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["name"] == created["name"]
    assert fetched.json()["status"] == "ACTIVE"


def test_a_duplicate_project_key_is_refused(admin_client: httpx.Client) -> None:
    key = _unique_key()
    _create(admin_client, key)
    response = admin_client.post("/api/v1/projects", json={"name": "Second", "projectKey": key})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


def test_the_list_is_paged(admin_client: httpx.Client) -> None:
    for _ in range(3):
        _create(admin_client, _unique_key())
    page = admin_client.get("/api/v1/projects?limit=2").json()
    assert len(page["items"]) == 2
    assert page["nextCursor"]
    following = admin_client.get(f"/api/v1/projects?limit=2&cursor={page['nextCursor']}").json()
    first_ids = {item["id"] for item in page["items"]}
    assert first_ids.isdisjoint({item["id"] for item in following["items"]})


def test_a_project_is_updated(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _unique_key())
    response = admin_client.patch(
        f"/api/v1/projects/{created['id']}", json={"description": "Now described"}
    )
    assert response.status_code == 200
    assert response.json()["description"] == "Now described"


def test_deleting_archives_rather_than_removes(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _unique_key())
    assert admin_client.delete(f"/api/v1/projects/{created['id']}").status_code == 204
    fetched = admin_client.get(f"/api/v1/projects/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "ARCHIVED"


def test_an_unknown_project_is_not_found(admin_client: httpx.Client) -> None:
    response = admin_client.get(f"/api/v1/projects/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_a_viewer_may_read_but_not_write(
    admin_client: httpx.Client, viewer_client: httpx.Client
) -> None:
    created = _create(admin_client, _unique_key())
    assert viewer_client.get(f"/api/v1/projects/{created['id']}").status_code == 200
    refused = viewer_client.post(
        "/api/v1/projects", json={"name": "Nope", "projectKey": _unique_key()}
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "PERMISSION_DENIED"


async def test_creating_a_project_writes_an_audit_row(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _unique_key())
    async with session_for_org(_organization(admin_client)) as session:
        rows: int = await session.scalar(
            sa.text(
                "SELECT count(*) FROM audit_logs "
                "WHERE action = 'project.created' AND entity_id = :id"
            ),
            {"id": uuid.UUID(str(created["id"]))},
        )
    assert rows == 1
