"""Preflight answers the whole question at once.

The demo test is addressed by its identifier rather than found by paging: it is
the oldest test there is, so a listing puts it last.
"""

import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from plimsoll_api.seed import DEMO_API_HOST, DEMO_TEST_ID

pytestmark = pytest.mark.integration


def _checks(body: dict[str, Any]) -> dict[str, str]:
    return {check["code"]: check["status"] for check in body["checks"]}


def _detail(body: dict[str, Any], code: str) -> str:
    detail: str = next(check["detail"] for check in body["checks"] if check["code"] == code)
    return detail


def _validate(client: httpx.Client, test_id: object) -> dict[str, Any]:
    response = client.post(f"/api/v1/tests/{test_id}/validate")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _credential_id(client: httpx.Client, name: str) -> str | None:
    items = client.get("/api/v1/credentials?limit=200").json()["items"]
    return next((str(item["id"]) for item in items if item["name"] == name), None)


def _set_variable(client: httpx.Client, name: str, value: str) -> None:
    """Credentials are write-once, so a new value means delete and re-create."""
    existing = _credential_id(client, name)
    if existing is not None:
        assert client.delete(f"/api/v1/credentials/{existing}").status_code == 204
    created = client.post(
        "/api/v1/credentials", json={"name": name, "kind": "VARIABLE", "secret": value}
    )
    assert created.status_code == 201, created.text


@pytest.fixture
def api_host_elsewhere(admin_client: httpx.Client) -> Iterator[str]:
    """Points the plan's ${API_HOST} off the allowlist, and puts it back."""
    elsewhere = "api.elsewhere.invalid"
    _set_variable(admin_client, "API_HOST", elsewhere)
    try:
        yield elsewhere
    finally:
        _set_variable(admin_client, "API_HOST", DEMO_API_HOST)


def test_the_seeded_demo_test_passes_preflight(admin_client: httpx.Client) -> None:
    body = _validate(admin_client, DEMO_TEST_ID)
    assert body["ok"] is True, body["checks"]
    statuses = _checks(body)
    assert statuses["TARGET_ALLOWED"] == "PASS"
    assert statuses["CAPACITY"] == "PASS"
    assert set(statuses.values()) == {"PASS"}


def test_a_target_outside_the_allowlist_fails_that_check(
    admin_client: httpx.Client, api_host_elsewhere: str
) -> None:
    """The plan's ${API_HOST} resolves from the organisation's variables."""
    body = _validate(admin_client, DEMO_TEST_ID)
    assert body["ok"] is False
    assert _checks(body)["TARGET_ALLOWED"] == "FAIL"
    assert api_host_elsewhere in _detail(body, "TARGET_ALLOWED")


def test_an_empty_allowlist_fails_the_target_check(admin_client: httpx.Client) -> None:
    assert admin_client.put("/api/v1/target-policy", json={"allowlist": []}).status_code == 200
    try:
        body = _validate(admin_client, DEMO_TEST_ID)
        assert _checks(body)["TARGET_ALLOWED"] == "FAIL"
        assert body["ok"] is False
    finally:
        admin_client.put("/api/v1/target-policy", json={"allowlist": [DEMO_API_HOST]})


def test_a_variable_with_no_stored_value_fails_that_check(admin_client: httpx.Client) -> None:
    """The value is not read here; only its existence is. It is injected at run
    start, in memory, and never written into the plan."""
    token = _credential_id(admin_client, "API_TOKEN")
    assert token is not None
    assert admin_client.delete(f"/api/v1/credentials/{token}").status_code == 204
    try:
        body = _validate(admin_client, DEMO_TEST_ID)
        assert _checks(body)["VARIABLES_PRESENT"] == "FAIL"
        assert "API_TOKEN" in _detail(body, "VARIABLES_PRESENT")
    finally:
        _set_variable(admin_client, "API_TOKEN", "demo-api-token")


def test_a_plan_pinned_to_a_commit_passes(admin_client: httpx.Client) -> None:
    """Pinning a commit is what a run does; preflight must accept one."""
    project_id = str(
        admin_client.post(
            "/api/v1/projects",
            json={"name": "Pinned", "projectKey": f"P{uuid.uuid4().hex[:8].upper()}"},
        ).json()["id"]
    )
    repo_id = str(
        admin_client.post(
            f"/api/v1/projects/{project_id}/script-repos",
            json={
                "name": f"repo-{uuid.uuid4().hex[:6]}",
                "repoUrl": "http://script-fixture/public/plans.git",
                "planPath": "perf/checkout.jmx",
                "defaultRef": "main",
            },
        ).json()["id"]
    )
    commit_sha = admin_client.post(
        f"/api/v1/script-repos/{repo_id}/versions", json={"ref": "main"}
    ).json()["commitSha"]
    pool_id = next(
        str(item["id"])
        for item in admin_client.get("/api/v1/generator-pools?limit=200").json()["items"]
        if item["name"] == "local-docker"
    )
    created = admin_client.post(
        f"/api/v1/projects/{project_id}/tests",
        json={
            "name": "Pinned to a commit",
            "configuration": {
                "virtualUsers": 10,
                "durationSeconds": 60,
                "rampUpSeconds": 10,
                "generatorPoolId": pool_id,
            },
            "plans": [
                {
                    "scriptRepoId": repo_id,
                    "pinnedRef": commit_sha,
                    "virtualUsers": 10,
                    "executionOrder": 1,
                }
            ],
            "slaRules": [],
        },
    ).json()

    body = _validate(admin_client, created["id"])
    assert body["ok"] is True, body["checks"]
    assert commit_sha[:8] in _detail(body, "SCRIPT_REF")


def test_every_failing_check_is_reported_at_once(admin_client: httpx.Client) -> None:
    project_id = str(
        admin_client.post(
            "/api/v1/projects",
            json={"name": "Preflight", "projectKey": f"F{uuid.uuid4().hex[:8].upper()}"},
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
    created = admin_client.post(
        f"/api/v1/projects/{project_id}/tests",
        json={
            "name": "Too big and unresolvable",
            "configuration": {
                "virtualUsers": 100_000,
                "durationSeconds": 60,
                "rampUpSeconds": 10,
                "generatorPoolId": pool_id,
            },
            "plans": [{"scriptRepoId": repo_id, "virtualUsers": 100_000, "executionOrder": 1}],
            "slaRules": [],
        },
    ).json()

    body = _validate(admin_client, created["id"])
    assert body["ok"] is False
    statuses = _checks(body)
    assert statuses["SCRIPT_REF"] == "FAIL"
    assert statuses["CAPACITY"] == "FAIL"
    # Every check is reported, including the ones that could not run.
    assert set(statuses) == {
        "TEST_STRUCTURE",
        "SCRIPT_REF",
        "PLAN_PARSES",
        "VARIABLES_PRESENT",
        "TARGET_ALLOWED",
        "CAPACITY",
    }
    assert statuses["PLAN_PARSES"] == "SKIPPED"


def test_a_workload_its_plans_do_not_add_up_to_fails_structure(admin_client: httpx.Client) -> None:
    project_id = str(
        admin_client.post(
            "/api/v1/projects",
            json={"name": "Mismatch", "projectKey": f"M{uuid.uuid4().hex[:8].upper()}"},
        ).json()["id"]
    )
    repo_id = str(
        admin_client.post(
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
        for item in admin_client.get("/api/v1/generator-pools?limit=200").json()["items"]
        if item["name"] == "local-docker"
    )
    created = admin_client.post(
        f"/api/v1/projects/{project_id}/tests",
        json={
            "name": "Half allocated",
            "configuration": {
                "virtualUsers": 100,
                "durationSeconds": 60,
                "rampUpSeconds": 10,
                "generatorPoolId": pool_id,
            },
            "plans": [{"scriptRepoId": repo_id, "virtualUsers": 40, "executionOrder": 1}],
            "slaRules": [],
        },
    ).json()

    body = _validate(admin_client, created["id"])
    assert _checks(body)["TEST_STRUCTURE"] == "FAIL"
    assert "40" in _detail(body, "TEST_STRUCTURE")
    # A structural failure does not stop the rest from being reported.
    assert _checks(body)["SCRIPT_REF"] == "PASS"


def test_an_unknown_test_is_not_found(admin_client: httpx.Client) -> None:
    assert admin_client.post(f"/api/v1/tests/{uuid.uuid4()}/validate").status_code == 404


def test_a_viewer_cannot_validate(viewer_client: httpx.Client) -> None:
    assert viewer_client.post(f"/api/v1/tests/{DEMO_TEST_ID}/validate").status_code == 403
