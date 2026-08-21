import uuid
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.integration

FIXTURE_URL = "http://script-fixture/public/plans.git"


def _project(client: httpx.Client) -> str:
    response = client.post(
        "/api/v1/projects",
        json={"name": "Repo holder", "projectKey": f"R{uuid.uuid4().hex[:8].upper()}"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _create(client: httpx.Client, project_id: str, **overrides: object) -> dict[str, Any]:
    body = {
        "name": f"repo-{uuid.uuid4().hex[:6]}",
        "repoUrl": FIXTURE_URL,
        "planPath": "perf/checkout.jmx",
        "defaultRef": "main",
    } | overrides
    response = client.post(f"/api/v1/projects/{project_id}/script-repos", json=body)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def test_a_repository_is_registered(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _project(admin_client))
    assert created["planPath"] == "perf/checkout.jmx"
    assert created["engine"] == "jmeter"
    assert created["credentialId"] is None


def test_it_is_listed_under_its_project(admin_client: httpx.Client) -> None:
    project_id = _project(admin_client)
    created = _create(admin_client, project_id)
    listed = admin_client.get(f"/api/v1/projects/{project_id}/script-repos").json()
    assert [item["id"] for item in listed["items"]] == [created["id"]]


def test_a_repository_in_an_unknown_project_is_not_found(admin_client: httpx.Client) -> None:
    response = admin_client.post(
        f"/api/v1/projects/{uuid.uuid4()}/script-repos",
        json={"name": "orphan", "repoUrl": FIXTURE_URL, "planPath": "perf/checkout.jmx"},
    )
    assert response.status_code == 404


def test_an_unsupported_url_scheme_is_refused(admin_client: httpx.Client) -> None:
    response = admin_client.post(
        f"/api/v1/projects/{_project(admin_client)}/script-repos",
        json={"name": "bad", "repoUrl": "file:///etc/passwd", "planPath": "p.jmx"},
    )
    assert response.status_code == 422


def test_a_plan_path_escaping_the_repository_is_refused(admin_client: httpx.Client) -> None:
    response = admin_client.post(
        f"/api/v1/projects/{_project(admin_client)}/script-repos",
        json={"name": "bad", "repoUrl": FIXTURE_URL, "planPath": "../../etc/passwd"},
    )
    assert response.status_code == 422


def test_an_unknown_credential_is_refused(admin_client: httpx.Client) -> None:
    response = admin_client.post(
        f"/api/v1/projects/{_project(admin_client)}/script-repos",
        json={
            "name": "bad",
            "repoUrl": FIXTURE_URL,
            "planPath": "perf/checkout.jmx",
            "credentialId": str(uuid.uuid4()),
        },
    )
    assert response.status_code == 422


def test_a_repository_is_updated_and_archived(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _project(admin_client))
    updated = admin_client.patch(
        f"/api/v1/script-repos/{created['id']}", json={"defaultRef": "broken"}
    )
    assert updated.status_code == 200
    assert updated.json()["defaultRef"] == "broken"

    assert admin_client.delete(f"/api/v1/script-repos/{created['id']}").status_code == 204
    assert admin_client.get(f"/api/v1/script-repos/{created['id']}").json()["status"] == "ARCHIVED"


def test_a_viewer_cannot_register_one(
    admin_client: httpx.Client, viewer_client: httpx.Client
) -> None:
    project_id = _project(admin_client)
    response = viewer_client.post(
        f"/api/v1/projects/{project_id}/script-repos",
        json={"name": "nope", "repoUrl": FIXTURE_URL, "planPath": "perf/checkout.jmx"},
    )
    assert response.status_code == 403
