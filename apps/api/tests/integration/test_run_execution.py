"""The whole path, over HTTP: a run starts, containers appear, it completes."""

import time
import uuid
from typing import Any

import httpx
import pytest

pytestmark = pytest.mark.integration

TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


def _short_test(client: httpx.Client, seconds: int = 10, users: int = 2) -> str:
    project_id = str(
        client.post(
            "/api/v1/projects",
            json={"name": "Execution", "projectKey": f"X{uuid.uuid4().hex[:8].upper()}"},
        ).json()["id"]
    )
    repo_id = str(
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
    pool_id = next(
        str(item["id"])
        for item in client.get("/api/v1/generator-pools?limit=200").json()["items"]
        if item["name"] == "local-docker"
    )
    return str(
        client.post(
            f"/api/v1/projects/{project_id}/tests",
            json={
                "name": "Short run",
                "configuration": {
                    "virtualUsers": users,
                    "durationSeconds": seconds,
                    "rampUpSeconds": 1,
                    "generatorPoolId": pool_id,
                },
                "plans": [{"scriptRepoId": repo_id, "virtualUsers": users, "executionOrder": 1}],
                "slaRules": [],
            },
        ).json()["id"]
    )


def _await_status(
    client: httpx.Client, run_id: str, wanted: set[str], timeout: int = 120
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = client.get(f"/api/v1/runs/{run_id}/status").json()
        if last["status"] in wanted:
            return last
        time.sleep(2)
    raise AssertionError(f"run stayed at {last.get('status')}: {last}")


def test_a_run_reaches_running_and_then_completes(admin_client: httpx.Client) -> None:
    test_id = _short_test(admin_client, seconds=10)
    run_id = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]

    running = _await_status(admin_client, run_id, {"RUNNING"})
    assert all(g["status"] in {"RUNNING", "READY"} for g in running["generators"])

    completed = _await_status(admin_client, run_id, TERMINAL)
    assert completed["status"] == "COMPLETED", completed
    assert completed["endedAt"] is not None

    # The bundle digest is part of what the run is pinned to, beside the commit
    # SHAs: a run that cannot say which bytes it executed is not reproducible.
    snapshot = admin_client.get(f"/api/v1/runs/{run_id}").json()["configurationSnapshot"]
    assert len(snapshot["bundleSha256"]) == 64, snapshot.get("bundleSha256")


def test_every_generator_registers_and_heartbeats(admin_client: httpx.Client) -> None:
    test_id = _short_test(admin_client, seconds=10, users=2)
    run_id = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
    running = _await_status(admin_client, run_id, {"RUNNING"})
    assert len(running["generators"]) >= 1
    assert all(g["lastHeartbeat"] is not None for g in running["generators"])
    _await_status(admin_client, run_id, TERMINAL)
