import uuid
from typing import Any

import httpx
import pytest

from plimsoll_api.seed import DEMO_TEST_ID

pytestmark = pytest.mark.integration


def _pool_id(client: httpx.Client) -> str:
    items = client.get("/api/v1/generator-pools?limit=200").json()["items"]
    return next(str(item["id"]) for item in items if item["name"] == "local-docker")


def _project_id(client: httpx.Client) -> str:
    return str(
        client.post(
            "/api/v1/projects",
            json={"name": "Tests", "projectKey": f"T{uuid.uuid4().hex[:8].upper()}"},
        ).json()["id"]
    )


def _repo_id(client: httpx.Client, project_id: str) -> str:
    return str(
        client.post(
            f"/api/v1/projects/{project_id}/script-repos",
            json={
                "name": f"repo-{uuid.uuid4().hex[:6]}",
                "repoUrl": "http://script-fixture/public/plans.git",
                "planPath": "perf/checkout.jmx",
                "defaultRef": "main",
            },
        ).json()["id"]
    )


def _body(client: httpx.Client, project_id: str) -> dict[str, Any]:
    return {
        "name": "Checkout peak",
        "configuration": {
            "virtualUsers": 200,
            "durationSeconds": 600,
            "rampUpSeconds": 60,
            "generatorPoolId": _pool_id(client),
        },
        "plans": [
            {
                "scriptRepoId": _repo_id(client, project_id),
                "pinnedRef": "main",
                "virtualUsers": 200,
                "executionOrder": 1,
            }
        ],
        "slaRules": [
            {
                "name": "p95 under 800ms",
                "metric": "p95",
                "operator": "lt",
                "threshold": 800,
                "unit": "ms",
                "severity": "ERROR",
            }
        ],
    }


def _create(client: httpx.Client, project_id: str, **overrides: Any) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/projects/{project_id}/tests", json=_body(client, project_id) | overrides
    )
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


def test_a_test_is_created_with_its_plans_and_rules(admin_client: httpx.Client) -> None:
    body = _create(admin_client, _project_id(admin_client))
    assert body["version"] == 1
    assert body["status"] == "DRAFT"
    assert len(body["plans"]) == 1
    assert body["slaRules"][0]["metric"] == "p95"


def test_it_is_read_back_whole(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _project_id(admin_client))
    fetched = admin_client.get(f"/api/v1/tests/{created['id']}").json()
    assert fetched["plans"] == created["plans"]
    assert fetched["slaRules"] == created["slaRules"]


def test_a_ramp_longer_than_the_duration_is_refused(admin_client: httpx.Client) -> None:
    project_id = _project_id(admin_client)
    body = _body(admin_client, project_id)
    body["configuration"]["rampUpSeconds"] = 1200
    response = admin_client.post(f"/api/v1/projects/{project_id}/tests", json=body)
    assert response.status_code == 422


def test_an_unknown_sla_metric_is_refused(admin_client: httpx.Client) -> None:
    project_id = _project_id(admin_client)
    body = _body(admin_client, project_id)
    body["slaRules"][0]["metric"] = "vibes"
    assert admin_client.post(f"/api/v1/projects/{project_id}/tests", json=body).status_code == 422


def test_a_test_without_a_plan_is_refused(admin_client: httpx.Client) -> None:
    project_id = _project_id(admin_client)
    body = _body(admin_client, project_id) | {"plans": []}
    assert admin_client.post(f"/api/v1/projects/{project_id}/tests", json=body).status_code == 422


def test_an_unknown_pool_is_refused(admin_client: httpx.Client) -> None:
    project_id = _project_id(admin_client)
    body = _body(admin_client, project_id)
    body["configuration"]["generatorPoolId"] = str(uuid.uuid4())
    response = admin_client.post(f"/api/v1/projects/{project_id}/tests", json=body)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"


def test_an_update_replaces_rules_and_keeps_plans(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _project_id(admin_client))
    updated = admin_client.patch(
        f"/api/v1/tests/{created['id']}",
        json={"version": created["version"], "slaRules": [], "name": "Renamed"},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["slaRules"] == []
    assert body["name"] == "Renamed"
    assert body["version"] == created["version"] + 1
    # plans was not in the body, so it is left alone.
    assert len(body["plans"]) == 1


def test_a_stale_version_conflicts(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _project_id(admin_client))
    first = admin_client.patch(
        f"/api/v1/tests/{created['id']}", json={"version": created["version"], "name": "First"}
    )
    assert first.status_code == 200
    stale = admin_client.patch(
        f"/api/v1/tests/{created['id']}", json={"version": created["version"], "name": "Second"}
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "CONFLICT"
    assert admin_client.get(f"/api/v1/tests/{created['id']}").json()["name"] == "First"


def test_a_test_is_archived(admin_client: httpx.Client) -> None:
    created = _create(admin_client, _project_id(admin_client))
    assert admin_client.delete(f"/api/v1/tests/{created['id']}").status_code == 204
    assert admin_client.get(f"/api/v1/tests/{created['id']}").json()["status"] == "ARCHIVED"


def test_the_seeded_demo_test_is_whole(admin_client: httpx.Client) -> None:
    """Addressed by its identifier rather than found by paging: the demo
    project is the oldest one there is, so a listing puts it last."""
    body = admin_client.get(f"/api/v1/tests/{DEMO_TEST_ID}").json()
    assert body["name"] == "Demo checkout test"
    assert body["configuration"]["virtualUsers"] == 20
    assert body["configuration"]["generatorPoolId"] == _pool_id(admin_client)
    assert len(body["plans"]) == 1
    assert [rule["metric"] for rule in body["slaRules"]] == ["p95"]


def test_a_viewer_cannot_create_one(
    admin_client: httpx.Client, viewer_client: httpx.Client
) -> None:
    project_id = _project_id(admin_client)
    body = _body(admin_client, project_id)
    assert viewer_client.post(f"/api/v1/projects/{project_id}/tests", json=body).status_code == 403
