import httpx
import pytest

pytestmark = pytest.mark.integration

SEEDED = ["demo-target"]


def test_the_seeded_policy_allows_only_the_demo_target(admin_client: httpx.Client) -> None:
    body = admin_client.get("/api/v1/target-policy").json()
    assert body["allowlist"] == SEEDED
    assert body["version"] >= 1


def test_a_put_creates_a_new_version(admin_client: httpx.Client) -> None:
    before = admin_client.get("/api/v1/target-policy").json()
    response = admin_client.put(
        "/api/v1/target-policy", json={"allowlist": ["demo-target", ".example.com"]}
    )
    assert response.status_code == 200, response.text
    assert response.json()["version"] == before["version"] + 1
    assert response.json()["allowlist"] == ["demo-target", ".example.com"]

    # Restore, so the rest of the suite sees the seeded policy.
    admin_client.put("/api/v1/target-policy", json={"allowlist": SEEDED})


def test_a_dangerous_entry_is_refused(admin_client: httpx.Client) -> None:
    response = admin_client.put(
        "/api/v1/target-policy", json={"allowlist": ["demo-target", "169.254.169.254"]}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_FAILED"
    assert admin_client.get("/api/v1/target-policy").json()["allowlist"] == SEEDED


def test_an_empty_allowlist_is_accepted_and_permits_nothing(admin_client: httpx.Client) -> None:
    assert admin_client.put("/api/v1/target-policy", json={"allowlist": []}).status_code == 200
    assert admin_client.get("/api/v1/target-policy").json()["allowlist"] == []
    admin_client.put("/api/v1/target-policy", json={"allowlist": SEEDED})


def test_a_viewer_may_read_but_not_write(viewer_client: httpx.Client) -> None:
    assert viewer_client.get("/api/v1/target-policy").status_code == 200
    assert viewer_client.put("/api/v1/target-policy", json={"allowlist": []}).status_code == 403
