"""The S3 promise, end to end: a defined test generates real load and leaves
artifacts an operator can download."""

import csv
import io
import shutil
import subprocess

import httpx
import pytest

from tests.integration.test_run_execution import TERMINAL, _await_status, _short_test

pytestmark = pytest.mark.integration

DOCKER = shutil.which("docker") or "docker"


def test_a_test_runs_and_leaves_a_jtl_full_of_samples(admin_client: httpx.Client) -> None:
    test_id = _short_test(admin_client, seconds=20, users=4)

    created = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()
    assert len(created["configurationSnapshot"]["plans"][0]["commitSha"]) == 40

    final = _await_status(admin_client, created["id"], TERMINAL, timeout=300)
    assert final["status"] == "COMPLETED", final
    assert final["degraded"] is False

    listed = admin_client.get(f"/api/v1/runs/{created['id']}/artifacts").json()
    assert {"results.jtl", "jmeter.log"} <= {item["name"] for item in listed["items"]}

    location = admin_client.get(
        f"/api/v1/runs/{created['id']}/artifacts/results.jtl", follow_redirects=False
    ).headers["location"]
    jtl = httpx.get(location).text

    rows = list(csv.DictReader(io.StringIO(jtl)))
    assert rows, "the JTL has no samples: JMeter produced nothing"
    assert {"timeStamp", "elapsed", "label", "success"} <= set(rows[0])
    # The load reached the demo target, not something else.
    assert any(row["success"] == "true" for row in rows)
    assert any("demo-target" in row["URL"] for row in rows), "no sample reached the demo target"


def test_every_generator_ran_the_same_bytes(admin_client: httpx.Client) -> None:
    """The bundle digest is what makes that a fact rather than a hope."""
    test_id = _short_test(admin_client, seconds=15, users=2)
    run_id = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
    _await_status(admin_client, run_id, TERMINAL, timeout=300)

    snapshot = admin_client.get(f"/api/v1/runs/{run_id}").json()["configurationSnapshot"]
    assert len(snapshot["bundleSha256"]) == 64, snapshot.get("bundleSha256")


def test_the_generators_are_gone_afterwards(admin_client: httpx.Client) -> None:
    test_id = _short_test(admin_client, seconds=15, users=2)
    run_id = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
    _await_status(admin_client, run_id, TERMINAL, timeout=300)

    remaining = subprocess.run(  # noqa: S603
        [DOCKER, "ps", "-aq", "--filter", f"label=plimsoll.run={run_id}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert remaining == [], f"generators outlived their run: {remaining}"


def test_no_durable_credential_reaches_a_generator(admin_client: httpx.Client) -> None:
    """The agent holds presigned URLs and nothing else.

    A Git token or an object-store key inside a container running a
    user-supplied plan is the failure the whole staging design exists to avoid.
    """
    test_id = _short_test(admin_client, seconds=60, users=2)
    run_id = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
    _await_status(admin_client, run_id, {"RUNNING"}, timeout=300)

    container = subprocess.run(  # noqa: S603
        [DOCKER, "ps", "-q", "--filter", f"label=plimsoll.run={run_id}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()[0]
    environment = subprocess.run(  # noqa: S603
        [DOCKER, "inspect", container, "--format", "{{range .Config.Env}}{{println .}}{{end}}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    names = {line.split("=", 1)[0] for line in environment.splitlines() if "=" in line}
    assert (
        not {
            "PLIMSOLL_S3_ACCESS_KEY",
            "PLIMSOLL_S3_SECRET_KEY",
            "PLIMSOLL_CREDENTIAL_KEY",
            "PLIMSOLL_DATABASE_URL",
            "PLIMSOLL_JWT_SECRET",
        }
        & names
    ), f"a durable credential reached a generator: {names}"
    # The fixture's Git password must not be there under any name.
    assert "plimsoll-fixture-token" not in environment

    admin_client.post(f"/api/v1/runs/{run_id}/cancel")
