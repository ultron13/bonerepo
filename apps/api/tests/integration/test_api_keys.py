"""Keys for the things that are not people.

A pipeline cannot hold a password, and giving it one means a human credential
lives in CI for ever. A key is scoped to what that pipeline does, shown once,
and revocable without touching anyone's account.
"""

import uuid

import httpx
import pytest

from tests.integration.conftest import API_URL

pytestmark = pytest.mark.integration


def _create(client: httpx.Client, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": f"ci-{uuid.uuid4().hex[:8]}",
        # What a pipeline that starts runs actually needs, and nothing else.
        "scopes": ["project.read", "test.read", "test.execute"],
    }
    body.update(overrides)
    return dict(client.post("/api/v1/api-keys", json=body).json())


def _as_key(secret: str) -> httpx.Client:
    client = httpx.Client(base_url=API_URL, timeout=30)
    client.headers["Authorization"] = f"Bearer {secret}"
    return client


def test_a_key_is_shown_once_and_never_again(admin_client: httpx.Client) -> None:
    """Stored as a hash. A leaked key is revoked, not recovered."""
    created = _create(admin_client)
    assert str(created["secret"]).startswith("plim_")

    listed = admin_client.get("/api/v1/api-keys").json()["items"]
    mine = next(item for item in listed if item["id"] == created["id"])
    assert "secret" not in mine
    # The prefix is kept so a key can be recognised in a list without being usable.
    assert mine["prefix"] and mine["prefix"] in str(created["secret"])


def test_a_key_authenticates(admin_client: httpx.Client) -> None:
    created = _create(admin_client)
    with _as_key(str(created["secret"])) as client:
        assert client.get("/api/v1/projects?limit=1").status_code == 200


def test_a_key_is_confined_to_its_scopes(admin_client: httpx.Client) -> None:
    """The point of scopes: a pipeline that starts runs cannot rewrite the
    target policy, however the key leaks."""
    created = _create(admin_client, scopes=["test.read"])
    with _as_key(str(created["secret"])) as client:
        # Granted test.read and nothing else: a project listing is not its
        # business, and neither is the target policy.
        assert client.get("/api/v1/projects?limit=1").status_code == 403
        assert (
            client.put(
                "/api/v1/target-policy", json={"allowlist": ["evil.example.com"]}
            ).status_code
            == 403
        )


def test_an_unknown_key_is_refused() -> None:
    with _as_key("plim_live_" + "0" * 40) as client:
        assert client.get("/api/v1/projects?limit=1").status_code == 401


def test_a_revoked_key_stops_working(admin_client: httpx.Client) -> None:
    created = _create(admin_client)
    with _as_key(str(created["secret"])) as client:
        assert client.get("/api/v1/projects?limit=1").status_code == 200

    admin_client.delete(f"/api/v1/api-keys/{created['id']}")

    with _as_key(str(created["secret"])) as client:
        assert client.get("/api/v1/projects?limit=1").status_code == 401


def test_an_expired_key_stops_working(admin_client: httpx.Client) -> None:
    created = _create(admin_client, expiresInDays=0)
    with _as_key(str(created["secret"])) as client:
        assert client.get("/api/v1/projects?limit=1").status_code == 401


def test_using_a_key_records_when(admin_client: httpx.Client) -> None:
    """An operator auditing keys needs to know which are still in use before
    revoking one and breaking a pipeline nobody remembers owning."""
    created = _create(admin_client)
    with _as_key(str(created["secret"])) as client:
        client.get("/api/v1/projects?limit=1")

    listed = admin_client.get("/api/v1/api-keys").json()["items"]
    mine = next(item for item in listed if item["id"] == created["id"])
    assert mine["lastUsedAt"] is not None


def test_a_key_cannot_be_granted_more_than_its_creator_holds(
    viewer_client: httpx.Client,
) -> None:
    """Otherwise a key is a privilege-escalation tool: a viewer mints an
    administrator and the role system means nothing."""
    response = viewer_client.post(
        "/api/v1/api-keys", json={"name": "escalate", "scopes": ["admin.system"]}
    )
    assert response.status_code == 403


def test_creating_a_key_is_audited(admin_client: httpx.Client) -> None:
    created = _create(admin_client)
    entries = admin_client.get("/api/v1/audit-logs?action=api_key.created&limit=20").json()
    assert any(item["entityId"] == created["id"] for item in entries["items"])
