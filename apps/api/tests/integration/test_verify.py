import uuid
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from plimsoll_api.db.session import session_for_org

pytestmark = pytest.mark.integration

PUBLIC = "http://script-fixture/public/plans.git"
PRIVATE = "http://script-fixture/private/plans.git"


def _repo(client: httpx.Client, **overrides: object) -> dict[str, Any]:
    project = client.post(
        "/api/v1/projects",
        json={"name": "Verify", "projectKey": f"V{uuid.uuid4().hex[:8].upper()}"},
    ).json()
    body = {
        "name": f"repo-{uuid.uuid4().hex[:6]}",
        "repoUrl": PUBLIC,
        "planPath": "perf/checkout.jmx",
        "defaultRef": "main",
    } | overrides
    created: dict[str, Any] = client.post(
        f"/api/v1/projects/{project['id']}/script-repos", json=body
    ).json()
    return created


def _fixture_credential_id(client: httpx.Client) -> str:
    items = client.get("/api/v1/credentials?limit=200").json()["items"]
    return next(str(item["id"]) for item in items if item["name"] == "fixture-git-token")


def _verify(client: httpx.Client, repo_id: object) -> httpx.Response:
    return client.post(f"/api/v1/script-repos/{repo_id}/verify")


def test_a_conformant_repository_verifies_clean(admin_client: httpx.Client) -> None:
    response = _verify(admin_client, _repo(admin_client)["id"])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True, body["findings"]
    assert len(body["commitSha"]) == 40
    assert body["plan"]["threadGroups"] == ["Shoppers"]
    assert body["plan"]["dataFiles"] == ["data/users.csv"]
    assert {"API_HOST", "API_TOKEN"} <= set(body["plan"]["variables"])
    # ${API_HOST} cannot be checked without a test context, so verify says so
    # as a warning rather than passing it over in silence.
    unresolved = [f for f in body["findings"] if f["code"] == "TARGET_UNRESOLVED"]
    assert unresolved and unresolved[0]["severity"] == "WARNING"


def test_the_seeded_credential_opens_the_private_repository(admin_client: httpx.Client) -> None:
    repo = _repo(admin_client, repoUrl=PRIVATE, credentialId=_fixture_credential_id(admin_client))
    body = _verify(admin_client, repo["id"]).json()
    assert body["ok"] is True, body["findings"]


def test_a_missing_credential_reaches_repo_unreachable(admin_client: httpx.Client) -> None:
    response = _verify(admin_client, _repo(admin_client, repoUrl=PRIVATE)["id"])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REPO_UNREACHABLE"


def test_an_unknown_ref_reaches_repo_unreachable(admin_client: httpx.Client) -> None:
    response = _verify(admin_client, _repo(admin_client, defaultRef="no-such-branch")["id"])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REPO_UNREACHABLE"


def test_a_declared_but_absent_data_file_is_a_finding(admin_client: httpx.Client) -> None:
    """Reachable but non-conformant is 200 with findings, not an error."""
    response = _verify(admin_client, _repo(admin_client, defaultRef="broken")["id"])
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "DATA_FILE_MISSING" in {finding["code"] for finding in body["findings"]}
    assert body["plan"] is not None, "the plan still parsed; report what was learned"


def test_a_missing_plan_is_a_finding(admin_client: httpx.Client) -> None:
    body = _verify(admin_client, _repo(admin_client, planPath="perf/nowhere.jmx")["id"]).json()
    assert body["ok"] is False
    assert {"PLAN_MISSING"} == {finding["code"] for finding in body["findings"]}


def test_the_manifest_plugins_are_reported(admin_client: httpx.Client) -> None:
    """The fixture declares none; the field exists for S3 to resolve against."""
    body = _verify(admin_client, _repo(admin_client)["id"]).json()
    assert body["plugins"] == []


async def test_verifying_is_audited(admin_client: httpx.Client, admin_org: uuid.UUID) -> None:
    repo = _repo(admin_client)
    _verify(admin_client, repo["id"])

    async with session_for_org(admin_org) as session:
        rows: int = await session.scalar(
            sa.text(
                "SELECT count(*) FROM audit_logs "
                "WHERE action = 'script_repo.verified' AND entity_id = :id"
            ),
            {"id": uuid.UUID(str(repo["id"]))},
        )
    assert rows == 1


def test_a_viewer_cannot_verify(admin_client: httpx.Client, viewer_client: httpx.Client) -> None:
    repo = _repo(admin_client)
    assert _verify(viewer_client, repo["id"]).status_code == 403
