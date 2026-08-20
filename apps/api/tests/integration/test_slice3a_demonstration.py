"""The S3a promise: a defined test becomes containers, and comes back clean."""

import os
import uuid

import httpx
import pytest
import redis

from plimsoll_api.messaging import RUNS_EXECUTION
from tests.integration.test_run_execution import TERMINAL, _await_status, _short_test
from tests.integration.test_run_failure import _containers_for

pytestmark = pytest.mark.integration


def _local_pool(client: httpx.Client) -> str:
    return next(
        str(item["id"])
        for item in client.get("/api/v1/generator-pools?limit=200").json()["items"]
        if item["name"] == "local-docker"
    )


def test_the_seeded_pool_reports_a_working_runtime(admin_client: httpx.Client) -> None:
    pool_id = _local_pool(admin_client)
    body = admin_client.post(f"/api/v1/generator-pools/{pool_id}/test-connection").json()
    assert body["ok"] is True, body["detail"]


def test_a_viewer_cannot_probe_a_pool(
    admin_client: httpx.Client, viewer_client: httpx.Client
) -> None:
    pool_id = _local_pool(admin_client)
    response = viewer_client.post(f"/api/v1/generator-pools/{pool_id}/test-connection")
    assert response.status_code == 403


def test_a_pool_naming_an_absent_image_reports_why(admin_client: httpx.Client) -> None:
    """The point of the probe: an operator gets the reason, not an exception."""
    pool_id = admin_client.post(
        "/api/v1/generator-pools",
        json={
            "name": f"missing-image-{uuid.uuid4().hex[:8]}",
            "runtime": "docker",
            "config": {"image": "ghcr.io/ultron13/no-such-image:absent"},
            "maxGenerators": 1,
            "maxVusPerGenerator": 1,
        },
    ).json()["id"]
    body = admin_client.post(f"/api/v1/generator-pools/{pool_id}/test-connection").json()
    assert body["ok"] is False
    assert "no-such-image" in body["detail"]


def test_a_test_becomes_a_run_and_returns(admin_client: httpx.Client) -> None:
    test_id = _short_test(admin_client, seconds=10, users=2)

    created = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()
    assert created["status"] == "QUEUED"
    assert len(created["configurationSnapshot"]["plans"][0]["commitSha"]) == 40

    running = _await_status(admin_client, created["id"], {"RUNNING"})
    assert running["generators"], "a running run has generators"

    final = _await_status(admin_client, created["id"], TERMINAL)
    assert final["status"] == "COMPLETED"
    assert final["degraded"] is False

    detail = admin_client.get(f"/api/v1/runs/{created['id']}").json()
    assert detail["summary"]["generators"] >= 1


def test_a_duplicate_execution_message_provisions_once(admin_client: httpx.Client) -> None:
    """At-least-once delivery is the contract, not a caveat to work around.

    Two copies of one run's message must produce that run's generators once --
    N containers, never 2N. The conditional QUEUED -> ALLOCATING transition is
    what refuses the second copy; this proves it against a real duplicate.
    """
    org_id = admin_client.get("/api/v1/auth/me").json()["organizationId"]
    test_id = _short_test(admin_client, seconds=30, users=2)
    created = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()
    run_id = created["id"]
    expected = len(created["configurationSnapshot"]["generators"])

    # A second delivery of the identical message, as a broker retry would send.
    client = redis.Redis.from_url(os.environ["PLIMSOLL_REDIS_URL"], decode_responses=True)
    try:
        client.xadd(RUNS_EXECUTION, {"runId": run_id, "organizationId": org_id})
    finally:
        client.close()

    _await_status(admin_client, run_id, {"RUNNING"})
    # Counted while the run is live, when a duplicate would have doubled it.
    assert len(_containers_for(run_id)) == expected
    assert len(_await_status(admin_client, run_id, {"RUNNING"})["generators"]) == expected

    _await_status(admin_client, run_id, TERMINAL)


def test_a_pool_sizes_the_generators_it_launches(admin_client: httpx.Client) -> None:
    """Sizing belongs with the capacity the pool already declares.

    A pool driving two thousand virtual users per generator needs a bigger
    container than one driving two hundred, and an operator who cannot say so
    is left with a default that is wrong for one of them.
    """
    import shutil
    import subprocess

    docker = shutil.which("docker") or "docker"
    pool_id = admin_client.post(
        "/api/v1/generator-pools",
        json={
            "name": f"sized-{uuid.uuid4().hex[:8]}",
            "runtime": "docker",
            "config": {
                "image": "ghcr.io/ultron13/generator:dev",
                "memoryLimit": "768m",
                "cpuLimit": 1,
            },
            "maxGenerators": 1,
            "maxVusPerGenerator": 2,
        },
    ).json()["id"]

    project_id = admin_client.post(
        "/api/v1/projects",
        json={"name": "Sized", "projectKey": f"Z{uuid.uuid4().hex[:8].upper()}"},
    ).json()["id"]
    repo_id = admin_client.post(
        f"/api/v1/projects/{project_id}/script-repos",
        json={
            "name": f"sized-{uuid.uuid4().hex[:6]}",
            "repoUrl": "http://script-fixture/public/plans.git",
            "planPath": "perf/checkout.jmx",
            "defaultRef": "main",
        },
    ).json()["id"]
    test_id = admin_client.post(
        f"/api/v1/projects/{project_id}/tests",
        json={
            "name": "sized",
            "configuration": {
                "virtualUsers": 2,
                "durationSeconds": 60,
                "rampUpSeconds": 1,
                "generatorPoolId": pool_id,
            },
            "plans": [{"scriptRepoId": repo_id, "virtualUsers": 2, "executionOrder": 1}],
            "slaRules": [],
        },
    ).json()["id"]

    run_id = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
    try:
        _await_status(admin_client, run_id, {"RUNNING"}, timeout=300)
        container = subprocess.run(  # noqa: S603
            [docker, "ps", "-q", "--filter", f"label=plimsoll.run={run_id}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()[0]
        applied = subprocess.run(  # noqa: S603
            [docker, "inspect", container, "--format", "{{.HostConfig.Memory}}"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert int(applied) == 768 * 1024 * 1024, applied
    finally:
        admin_client.post(f"/api/v1/runs/{run_id}/stop")
        _await_status(admin_client, run_id, TERMINAL, timeout=300)
