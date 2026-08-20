"""What a finished run leaves behind, and how it comes back."""

import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration


def test_an_unknown_run_has_no_artifacts(admin_client: httpx.Client) -> None:
    """Authorisation happens against the run row: the object store knows
    nothing about tenancy, so a run that does not exist offers nothing."""
    assert admin_client.get(f"/api/v1/runs/{uuid.uuid4()}/artifacts").status_code == 404


def test_a_finished_run_offers_its_jtl(admin_client: httpx.Client, completed_run: str) -> None:
    listed = admin_client.get(f"/api/v1/runs/{completed_run}/artifacts").json()
    names = {item["name"] for item in listed["items"]}
    assert "results.jtl" in names, listed
    assert all(item["size"] > 0 for item in listed["items"])


def test_an_artifact_downloads_through_a_redirect(
    admin_client: httpx.Client, completed_run: str
) -> None:
    response = admin_client.get(
        f"/api/v1/runs/{completed_run}/artifacts/results.jtl", follow_redirects=False
    )
    assert response.status_code == 302
    body = httpx.get(response.headers["location"]).text
    assert "timeStamp" in body.splitlines()[0]


def test_an_unknown_artifact_is_not_found(admin_client: httpx.Client, completed_run: str) -> None:
    assert (
        admin_client.get(f"/api/v1/runs/{completed_run}/artifacts/nothing.txt").status_code == 404
    )


def test_a_traversing_name_is_refused(admin_client: httpx.Client, completed_run: str) -> None:
    response = admin_client.get(f"/api/v1/runs/{completed_run}/artifacts/..%2F..%2Fbundle.tar.gz")
    assert response.status_code in (404, 422)


def test_a_viewer_may_download(viewer_client: httpx.Client, completed_run: str) -> None:
    """Reading results is a read. TEST_READ is enough."""
    assert viewer_client.get(f"/api/v1/runs/{completed_run}/artifacts").status_code == 200
