"""The S2 promise, end to end: define and validate a test over HTTP.

Nothing here reaches into the database. If this passes, a performance engineer
with nothing but the API can go from an empty project to a definite answer
about whether their test will run.
"""

import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration


def test_a_test_can_be_defined_and_validated_from_nothing(admin_client: httpx.Client) -> None:
    project = admin_client.post(
        "/api/v1/projects",
        json={"name": "Walking skeleton", "projectKey": f"W{uuid.uuid4().hex[:8].upper()}"},
    ).json()

    credential = admin_client.post(
        "/api/v1/credentials",
        json={
            "name": f"git-{uuid.uuid4().hex[:6]}",
            "kind": "GIT_TOKEN",
            "secret": "plimsoll:plimsoll-fixture-token",
        },
    ).json()

    repo = admin_client.post(
        f"/api/v1/projects/{project['id']}/script-repos",
        json={
            "name": "Checkout",
            "repoUrl": "http://script-fixture/private/plans.git",
            "planPath": "perf/checkout.jmx",
            "defaultRef": "main",
            "credentialId": credential["id"],
        },
    ).json()

    report = admin_client.post(f"/api/v1/script-repos/{repo['id']}/verify").json()
    assert report["ok"] is True, report["findings"]

    version = admin_client.post(
        f"/api/v1/script-repos/{repo['id']}/versions", json={"ref": "main"}
    ).json()
    assert len(version["commitSha"]) == 40

    pool_id = next(
        item["id"]
        for item in admin_client.get("/api/v1/generator-pools?limit=200").json()["items"]
        if item["name"] == "local-docker"
    )
    test = admin_client.post(
        f"/api/v1/projects/{project['id']}/tests",
        json={
            "name": "Checkout peak",
            "configuration": {
                "virtualUsers": 50,
                "durationSeconds": 300,
                "rampUpSeconds": 30,
                "generatorPoolId": pool_id,
            },
            "plans": [
                {
                    "scriptRepoId": repo["id"],
                    "pinnedRef": version["commitSha"],
                    "virtualUsers": 50,
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
        },
    ).json()

    validation = admin_client.post(f"/api/v1/tests/{test['id']}/validate").json()
    assert validation["ok"] is True, validation["checks"]

    # The commit the run would execute is pinned, not the branch that could
    # move under it between validating and running.
    plan = test["plans"][0]
    assert plan["pinnedRef"] == version["commitSha"]
