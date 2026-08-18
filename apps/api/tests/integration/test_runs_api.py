"""Starting a run: what the API promises before any container exists."""

import uuid

import httpx
import pytest

from plimsoll_api.seed import DEMO_TEST_ID

pytestmark = pytest.mark.integration


def _start(client: httpx.Client, test_id: object = DEMO_TEST_ID) -> httpx.Response:
    return client.post(f"/api/v1/tests/{test_id}/runs")


def test_a_run_starts_queued(admin_client: httpx.Client) -> None:
    response = _start(admin_client)
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "QUEUED"
    assert body["triggerSource"] == "API"
    assert body["degraded"] is False
    assert body["runNumber"] >= 1


def test_the_snapshot_pins_the_commit_that_will_execute(admin_client: httpx.Client) -> None:
    """Invariant 3: a branch that moves mid-run cannot change what runs."""
    snapshot = _start(admin_client).json()["configurationSnapshot"]
    assert len(snapshot["plans"][0]["commitSha"]) == 40
    assert snapshot["workload"]["virtualUsers"] == 20
    assert sum(g["users"] for g in snapshot["generators"]) == 20
    assert snapshot["targetPolicyVersion"] >= 1


def test_run_numbers_increase_within_a_project(admin_client: httpx.Client) -> None:
    first = _start(admin_client).json()["runNumber"]
    second = _start(admin_client).json()["runNumber"]
    assert second == first + 1


def test_a_run_is_read_back_and_listed(admin_client: httpx.Client) -> None:
    created = _start(admin_client).json()
    fetched = admin_client.get(f"/api/v1/runs/{created['id']}").json()
    assert fetched["id"] == created["id"]

    listed = admin_client.get(f"/api/v1/projects/{created['projectId']}/runs").json()
    assert created["id"] in [item["id"] for item in listed["items"]]


def test_the_status_endpoint_answers_cheaply(admin_client: httpx.Client) -> None:
    created = _start(admin_client).json()
    status = admin_client.get(f"/api/v1/runs/{created['id']}/status").json()
    assert status["status"] in {"QUEUED", "ALLOCATING", "STARTING", "RUNNING"}
    assert "generators" in status


def test_a_test_that_fails_preflight_starts_no_run(admin_client: httpx.Client) -> None:
    project_id = str(
        admin_client.post(
            "/api/v1/projects",
            json={"name": "Unrunnable", "projectKey": f"U{uuid.uuid4().hex[:8].upper()}"},
        ).json()["id"]
    )
    repo_id = str(
        admin_client.post(
            f"/api/v1/projects/{project_id}/script-repos",
            json={
                "name": f"repo-{uuid.uuid4().hex[:6]}",
                "repoUrl": "http://script-fixture/public/plans.git",
                "planPath": "perf/checkout.jmx",
                "defaultRef": "no-such-branch",
            },
        ).json()["id"]
    )
    pool_id = next(
        str(item["id"])
        for item in admin_client.get("/api/v1/generator-pools?limit=200").json()["items"]
        if item["name"] == "local-docker"
    )
    test_id = admin_client.post(
        f"/api/v1/projects/{project_id}/tests",
        json={
            "name": "Cannot run",
            "configuration": {
                "virtualUsers": 10,
                "durationSeconds": 30,
                "rampUpSeconds": 5,
                "generatorPoolId": pool_id,
            },
            "plans": [{"scriptRepoId": repo_id, "virtualUsers": 10, "executionOrder": 1}],
            "slaRules": [],
        },
    ).json()["id"]

    response = _start(admin_client, test_id)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "TEST_NOT_RUNNABLE"
    failing = [c["code"] for c in response.json()["error"]["details"]["checks"]]
    assert "SCRIPT_REF" in failing
    assert admin_client.get(f"/api/v1/projects/{project_id}/runs").json()["items"] == []


def test_an_unknown_test_cannot_be_run(admin_client: httpx.Client) -> None:
    assert _start(admin_client, uuid.uuid4()).status_code == 404


def test_a_viewer_cannot_start_a_run(viewer_client: httpx.Client) -> None:
    assert _start(viewer_client).status_code == 403
