"""Stopping, and what happens when a generator disappears."""

import shutil
import subprocess
import time

import httpx
import pytest

from tests.integration.test_run_execution import TERMINAL, _await_status, _short_test

pytestmark = pytest.mark.integration

# Resolved once, so the calls below name a full path rather than trusting PATH.
# docker is a stated prerequisite of the integration run, so its absence is a
# broken environment rather than a test to skip.
DOCKER = shutil.which("docker") or "docker"


def _containers_for(run_id: str) -> list[str]:
    """Every container the run created, running or exited."""
    return subprocess.run(  # noqa: S603
        [DOCKER, "ps", "-aq", "--filter", f"label=plimsoll.run={run_id}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()


def _await_no_containers(run_id: str, timeout: int = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _containers_for(run_id):
            return
        time.sleep(2)
    raise AssertionError(f"run {run_id} left generators behind: {_containers_for(run_id)}")


def test_stop_is_idempotent(admin_client: httpx.Client) -> None:
    """Invariant 5: repeating stop returns 200 and re-runs no side effect."""
    test_id = _short_test(admin_client, seconds=60)
    run_id = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
    _await_status(admin_client, run_id, {"RUNNING"})

    first = admin_client.post(f"/api/v1/runs/{run_id}/stop")
    second = admin_client.post(f"/api/v1/runs/{run_id}/stop")
    assert first.status_code == 200
    assert second.status_code == 200

    final = _await_status(admin_client, run_id, TERMINAL)
    assert final["status"] == "COMPLETED"


def test_stopping_a_finished_run_is_still_200(admin_client: httpx.Client) -> None:
    test_id = _short_test(admin_client, seconds=5)
    run_id = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
    _await_status(admin_client, run_id, TERMINAL)
    assert admin_client.post(f"/api/v1/runs/{run_id}/stop").status_code == 200


def test_cancel_abandons_the_run(admin_client: httpx.Client) -> None:
    test_id = _short_test(admin_client, seconds=60)
    run_id = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
    _await_status(admin_client, run_id, {"RUNNING"})

    assert admin_client.post(f"/api/v1/runs/{run_id}/cancel").status_code == 200
    final = _await_status(admin_client, run_id, TERMINAL)
    assert final["status"] == "CANCELLED"
    # The worker owns every container it created, whoever ended the run. A
    # cancel ends it without the worker's help, which is the case that leaks.
    _await_no_containers(run_id)


def test_a_killed_generator_never_looks_like_success(admin_client: httpx.Client) -> None:
    """The invariant test. A run that lost capacity must say so -- a result
    produced with less load than planned must never pass as a full one."""
    test_id = _short_test(admin_client, seconds=90, users=2)
    run_id = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
    _await_status(admin_client, run_id, {"RUNNING"})

    # The daemon is reached through the CLI rather than the SDK: this test
    # asserts on what an operator would see.
    killed = _containers_for(run_id)
    assert killed, "no generator containers were labelled with the run"
    subprocess.run([DOCKER, "kill", killed[0]], check=True, capture_output=True)  # noqa: S603

    final = _await_status(admin_client, run_id, TERMINAL, timeout=180)
    assert final["status"] == "FAILED" or final["degraded"] is True, final


def test_a_viewer_cannot_stop_a_run(
    admin_client: httpx.Client, viewer_client: httpx.Client
) -> None:
    test_id = _short_test(admin_client, seconds=15)
    run_id = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
    assert viewer_client.post(f"/api/v1/runs/{run_id}/stop").status_code == 403
    _await_status(admin_client, run_id, TERMINAL)
