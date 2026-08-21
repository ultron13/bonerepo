import uuid
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.integration

PUBLIC = "http://script-fixture/public/plans.git"


def _repo(client: httpx.Client) -> dict[str, Any]:
    project = client.post(
        "/api/v1/projects",
        json={"name": "Pinning", "projectKey": f"P{uuid.uuid4().hex[:8].upper()}"},
    ).json()
    created: dict[str, Any] = client.post(
        f"/api/v1/projects/{project['id']}/script-repos",
        json={
            "name": f"repo-{uuid.uuid4().hex[:6]}",
            "repoUrl": PUBLIC,
            "planPath": "perf/checkout.jmx",
            "defaultRef": "main",
        },
    ).json()
    return created


def _pin(client: httpx.Client, repo_id: object, **body: object) -> httpx.Response:
    return client.post(f"/api/v1/script-repos/{repo_id}/versions", json=body)


def test_a_ref_pins_to_a_commit(admin_client: httpx.Client) -> None:
    response = _pin(admin_client, _repo(admin_client)["id"], ref="main")
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["commitSha"]) == 40
    assert body["planPath"] == "perf/checkout.jmx"
    assert body["checksum"]


def test_pinning_the_same_commit_twice_returns_the_same_version(
    admin_client: httpx.Client,
) -> None:
    repo_id = _repo(admin_client)["id"]
    first = _pin(admin_client, repo_id, ref="main")
    second = _pin(admin_client, repo_id, ref="main")
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    listed = admin_client.get(f"/api/v1/script-repos/{repo_id}/versions").json()
    assert len(listed["items"]) == 1


def test_two_branches_pin_to_two_versions(admin_client: httpx.Client) -> None:
    repo_id = _repo(admin_client)["id"]
    assert _pin(admin_client, repo_id, ref="main").status_code == 201
    assert _pin(admin_client, repo_id, ref="broken").status_code == 201
    assert len(admin_client.get(f"/api/v1/script-repos/{repo_id}/versions").json()["items"]) == 2


def test_a_version_is_fetched_by_id(admin_client: httpx.Client) -> None:
    created = _pin(admin_client, _repo(admin_client)["id"], ref="main").json()
    fetched = admin_client.get(f"/api/v1/script-versions/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["commitSha"] == created["commitSha"]


def test_pinning_without_a_ref_uses_the_default(admin_client: httpx.Client) -> None:
    assert _pin(admin_client, _repo(admin_client)["id"]).status_code == 201


def test_an_unknown_ref_is_repo_unreachable(admin_client: httpx.Client) -> None:
    response = _pin(admin_client, _repo(admin_client)["id"], ref="no-such-branch")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REPO_UNREACHABLE"


def test_the_plan_summary_is_stored_with_the_version(admin_client: httpx.Client) -> None:
    """Preflight and the workload editor read this instead of cloning again."""
    created = _pin(admin_client, _repo(admin_client)["id"], ref="main").json()
    fetched = admin_client.get(f"/api/v1/script-versions/{created['id']}").json()
    assert fetched["planSummary"]["threadGroups"] == ["Shoppers"]


def test_a_viewer_cannot_pin(admin_client: httpx.Client, viewer_client: httpx.Client) -> None:
    repo_id = _repo(admin_client)["id"]
    assert _pin(viewer_client, repo_id, ref="main").status_code == 403
