"""Reading the audit trail.

A log that is written and cannot be read is a table. These tests are about
whether an operator asked for an account of what happened could produce one.
"""

import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration


def _make_a_project(client: httpx.Client) -> tuple[str, str]:
    key = f"A{uuid.uuid4().hex[:8].upper()}"
    project = client.post("/api/v1/projects", json={"name": "Audited", "projectKey": key}).json()
    return str(project["id"]), key


def test_a_write_appears_in_the_trail(admin_client: httpx.Client) -> None:
    project_id, _ = _make_a_project(admin_client)

    entries = admin_client.get("/api/v1/audit-logs?limit=50").json()["items"]
    match = next((item for item in entries if item["entityId"] == project_id), None)
    assert match is not None, "creating a project left no trace"
    assert match["action"] == "project.created"
    assert match["entityType"] == "project"
    assert match["userId"]
    assert match["createdAt"]


def test_the_trail_is_newest_first(admin_client: httpx.Client) -> None:
    _make_a_project(admin_client)
    _make_a_project(admin_client)

    stamps = [item["createdAt"] for item in admin_client.get("/api/v1/audit-logs").json()["items"]]
    assert stamps == sorted(stamps, reverse=True)


def test_the_trail_pages(admin_client: httpx.Client) -> None:
    for _ in range(3):
        _make_a_project(admin_client)

    first = admin_client.get("/api/v1/audit-logs?limit=2").json()
    assert len(first["items"]) == 2
    assert first["nextCursor"]

    second = admin_client.get(f"/api/v1/audit-logs?limit=2&cursor={first['nextCursor']}").json()
    assert len(second["items"]) >= 1
    # A page boundary must not repeat or skip an entry.
    assert not {item["id"] for item in first["items"]} & {item["id"] for item in second["items"]}


def test_the_trail_filters_by_action(admin_client: httpx.Client) -> None:
    _make_a_project(admin_client)
    entries = admin_client.get("/api/v1/audit-logs?action=project.created&limit=20").json()
    assert entries["items"]
    assert {item["action"] for item in entries["items"]} == {"project.created"}


def test_the_trail_filters_by_entity(admin_client: httpx.Client) -> None:
    project_id, _ = _make_a_project(admin_client)
    entries = admin_client.get(f"/api/v1/audit-logs?entityId={project_id}").json()
    assert entries["items"]
    assert {item["entityId"] for item in entries["items"]} == {project_id}


def test_a_viewer_cannot_read_the_trail(viewer_client: httpx.Client) -> None:
    """Who did what is administrative, not a read like any other: it names
    people, and a viewer is not entitled to that account."""
    assert viewer_client.get("/api/v1/audit-logs").status_code == 403


def test_an_unknown_action_returns_an_empty_page(admin_client: httpx.Client) -> None:
    """Empty is an answer. A filter that matches nothing is not an error."""
    body = admin_client.get("/api/v1/audit-logs?action=nothing.happened").json()
    assert body["items"] == []
