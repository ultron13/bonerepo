"""Starting a run: what the API promises before any container exists."""

import os
import uuid

import httpx
import pytest
import sqlalchemy as sa

from plimsoll_api.seed import DEMO_TEST_ID

OWNER_URL = os.environ.get(
    "PLIMSOLL_TEST_MIGRATION_URL",
    "postgresql+psycopg://plimsoll_owner:plimsoll_owner_dev@localhost:5432/plimsoll",
)

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


def test_recent_runs_span_the_organisation_not_one_page_of_projects(
    admin_client: httpx.Client,
) -> None:
    """The list a person lands on has to be the newest runs there are.

    Assembling it in the browser -- a page of projects, then a request per
    project -- gets slower with every project and stops being correct long
    before it stops being fast: once there are more projects than fit in one
    page, the newest run can belong to a project nobody fetched.
    """
    newest = admin_client.get("/api/v1/runs?limit=25").json()["items"]
    assert newest, "the demo seed and the suite have both produced runs"

    # Newest first, and genuinely ordered rather than grouped by project.
    stamps = [item["createdAt"] for item in newest]
    assert stamps == sorted(stamps, reverse=True)

    # The single newest run in the organisation, found without knowing which
    # project it belongs to -- which is the property the browser cannot have.
    projects = admin_client.get("/api/v1/projects?limit=100").json()["items"]
    assert len(projects) > 1, "the premise is that runs span more than one project"
    assert newest[0]["projectId"] not in {"", None}


def test_recent_runs_are_paginated_like_everything_else(admin_client: httpx.Client) -> None:
    first = admin_client.get("/api/v1/runs?limit=2").json()
    assert len(first["items"]) == 2
    if first.get("nextCursor"):
        second = admin_client.get(f"/api/v1/runs?limit=2&cursor={first['nextCursor']}").json()
        assert {item["id"] for item in second["items"]}.isdisjoint(
            {item["id"] for item in first["items"]}
        )


def test_a_viewer_may_read_recent_runs(viewer_client: httpx.Client) -> None:
    assert viewer_client.get("/api/v1/runs?limit=5").status_code == 200


def test_a_run_pins_the_pool_it_was_started_with(admin_client: httpx.Client) -> None:
    """Invariant 3, for the half of it that was missing.

    Commit SHAs, workload, allocation and SLA rules were pinned. The generator
    pool was not: the image, the runtime and the sizing were read live at
    provision time, minutes after the run was accepted. Editing a pool in that
    window changed what executed -- past the preflight that had already
    approved something else -- and switching its runtime stranded the
    generators, because teardown would go looking in the wrong place.
    """
    # The pool this test actually uses, not whichever one is listed first.
    configuration = admin_client.get(f"/api/v1/tests/{DEMO_TEST_ID}").json()["configuration"]
    pool_id = uuid.UUID(configuration["generatorPoolId"])
    before = admin_client.get(f"/api/v1/generator-pools/{pool_id}").json()

    run = admin_client.post(f"/api/v1/tests/{DEMO_TEST_ID}/runs").json()
    assert "id" in run, run

    try:
        # The edit a run must be immune to, in the window where it can happen.
        assert (
            admin_client.patch(
                f"/api/v1/generator-pools/{pool_id}",
                json={"config": {**before["config"], "image": "example.invalid/moved:9.9.9"}},
            ).status_code
            == 200
        )

        engine = sa.create_engine(OWNER_URL)
        with engine.begin() as connection:
            connection.execute(
                sa.text("SELECT set_config('app.current_org_id', :org, true)"),
                {
                    "org": str(
                        uuid.UUID(admin_client.get("/api/v1/auth/me").json()["organizationId"])
                    )
                },
            )
            snapshot = connection.execute(
                sa.text("SELECT configuration_snapshot FROM test_runs WHERE id = :id"),
                {"id": uuid.UUID(run["id"])},
            ).scalar_one()
        engine.dispose()

        pinned = snapshot.get("pool")
        assert pinned is not None, "the run records the pool it was started with"
        assert pinned["image"] == before["config"]["image"]
        assert pinned["runtime"] == before["runtime"]
        assert pinned["id"] == str(pool_id)
    finally:
        admin_client.patch(f"/api/v1/generator-pools/{pool_id}", json={"config": before["config"]})
        admin_client.post(f"/api/v1/runs/{run['id']}/cancel")
