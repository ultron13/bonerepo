"""A run is judged at completion, against merged data."""

import time
import uuid
from typing import Any

import httpx
import pytest

from tests.integration.test_run_execution import TERMINAL, _await_status

pytestmark = pytest.mark.integration


def _test_with_rule(client: httpx.Client, rule: dict[str, Any], seconds: int = 15) -> str:
    project_id = client.post(
        "/api/v1/projects",
        json={"name": "SLA", "projectKey": f"S{uuid.uuid4().hex[:8].upper()}"},
    ).json()["id"]
    repo_id = client.post(
        f"/api/v1/projects/{project_id}/script-repos",
        json={
            "name": f"sla-{uuid.uuid4().hex[:6]}",
            "repoUrl": "http://script-fixture/public/plans.git",
            "planPath": "perf/checkout.jmx",
            "defaultRef": "main",
        },
    ).json()["id"]
    pool_id = next(
        str(item["id"])
        for item in client.get("/api/v1/generator-pools?limit=200").json()["items"]
        if item["name"] == "local-docker"
    )
    return str(
        client.post(
            f"/api/v1/projects/{project_id}/tests",
            json={
                "name": "Judged",
                "configuration": {
                    "virtualUsers": 2,
                    "durationSeconds": seconds,
                    "rampUpSeconds": 1,
                    "generatorPoolId": pool_id,
                },
                "plans": [{"scriptRepoId": repo_id, "virtualUsers": 2, "executionOrder": 1}],
                "slaRules": [rule],
            },
        ).json()["id"]
    )


def _verdict(client: httpx.Client, test_id: str) -> dict[str, Any]:
    """The outcome and its per-rule breakdown, as a caller reads them.

    The verdict is written a moment after the run ends, once the last window
    has been ingested -- so this waits for it rather than assuming it is
    already there.
    """
    run_id = client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
    _await_status(client, run_id, TERMINAL, timeout=300)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/runs/{run_id}").json()
        if body.get("slaResult"):
            return {
                "outcome": body["slaResult"],
                **(body.get("summary") or {}).get("sla", {}),
            }
        time.sleep(2)
    raise AssertionError("no SLA verdict was ever recorded")


def test_a_rule_that_holds_reports_a_passing_run(admin_client: httpx.Client) -> None:
    test_id = _test_with_rule(
        admin_client,
        {
            "name": "Browse is quick",
            "metric": "p95",
            "entity": "Browse",
            "operator": "lt",
            "threshold": 5000,
            "unit": "ms",
            "severity": "ERROR",
        },
    )
    result = _verdict(admin_client, test_id)
    assert result["outcome"] == "PASS", result
    assert result["rules"][0]["verdict"] == "PASS"
    assert result["rules"][0]["actual"] is not None


def test_a_rule_that_does_not_hold_fails_the_run(admin_client: httpx.Client) -> None:
    """The demo target cannot serve Browse in under a millisecond."""
    test_id = _test_with_rule(
        admin_client,
        {
            "name": "Browse under 1ms",
            "metric": "p95",
            "entity": "Browse",
            "operator": "lt",
            "threshold": 1,
            "unit": "ms",
            "severity": "ERROR",
        },
    )
    result = _verdict(admin_client, test_id)
    assert result["outcome"] == "FAIL", result
    assert result["rules"][0]["verdict"] == "FAIL"


def test_a_rule_naming_an_absent_transaction_is_skipped(admin_client: httpx.Client) -> None:
    """Never a pass: nothing was measured against the threshold."""
    test_id = _test_with_rule(
        admin_client,
        {
            "name": "Ghost is quick",
            "metric": "p95",
            "entity": "NoSuchTransaction",
            "operator": "lt",
            "threshold": 1,
            "unit": "ms",
            "severity": "ERROR",
        },
    )
    result = _verdict(admin_client, test_id)
    assert result["rules"][0]["verdict"] == "SKIPPED", result
    assert result["outcome"] == "PASS"
