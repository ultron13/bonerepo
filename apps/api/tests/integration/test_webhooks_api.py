"""Subscribing to what happens here, and where a subscription may point.

The delivery behaviour -- signing, retries, suspension -- is covered by unit
tests against a transport that can misbehave on demand. This covers the
surface an operator touches, and the refusals that make it safe to expose.
"""

from __future__ import annotations

import os
from typing import Any, cast

import httpx
import pytest

pytestmark = pytest.mark.integration


def _create(client: httpx.Client, **overrides: object) -> httpx.Response:
    body: dict[str, object] = {
        # Resolves, and to a public address. example.com is reserved for
        # exactly this and answers with one.
        "url": "https://example.com/hooks",
        "events": ["audit.*"],
    }
    body.update(overrides)
    return client.post("/api/v1/webhooks", json=body)


def test_a_subscription_shows_its_secret_once(admin_client: httpx.Client) -> None:
    created = _create(admin_client).json()
    assert created["secret"], created

    listed = admin_client.get("/api/v1/webhooks").json()["items"]
    mine = next(item for item in listed if item["id"] == created["id"])
    assert "secret" not in mine
    admin_client.delete(f"/api/v1/webhooks/{created['id']}")


def test_a_secret_is_generated_when_none_is_given(admin_client: httpx.Client) -> None:
    """A secret somebody has to invent is a secret somebody will invent badly."""
    created = _create(admin_client).json()
    assert len(created["secret"]) >= 32
    admin_client.delete(f"/api/v1/webhooks/{created['id']}")


@pytest.mark.parametrize(
    "url",
    [
        "https://localhost/hooks",
        "https://127.0.0.1/hooks",
    ],
)
def test_a_url_pointing_at_this_process_is_refused(admin_client: httpx.Client, url: str) -> None:
    """Refused whatever the configuration. This stack permits private
    addresses -- an on-premises SIEM is a real deployment -- and loopback is
    still where the control plane's own internals answer."""
    response = _create(admin_client, url=url)
    assert response.status_code == 422, response.text


def test_plain_http_is_refused(admin_client: httpx.Client) -> None:
    """A signed delivery over HTTP is readable by anything on the path, and
    the payload says who did what and when."""
    assert _create(admin_client, url="http://example.com/hooks").status_code == 422


def test_a_host_that_does_not_resolve_is_refused(admin_client: httpx.Client) -> None:
    """A subscription that can never deliver is better refused than stored:
    otherwise its first failure is a suspension nobody asked for."""
    response = _create(admin_client, url="https://nothing-here.invalid/hooks")
    assert response.status_code == 422
    assert "does not resolve" in response.text


def test_an_unknown_event_name_is_refused(admin_client: httpx.Client) -> None:
    """A typo would otherwise produce a subscription that is configured, looks
    right, and never fires."""
    assert _create(admin_client, events=["audit.everything"]).status_code == 422


def test_deleting_is_idempotent(admin_client: httpx.Client) -> None:
    created = _create(admin_client).json()
    assert admin_client.delete(f"/api/v1/webhooks/{created['id']}").status_code == 204
    assert admin_client.delete(f"/api/v1/webhooks/{created['id']}").status_code == 204


def test_a_viewer_cannot_see_or_create_subscriptions(viewer_client: httpx.Client) -> None:
    """A subscription is a copy of the audit trail leaving the building, and
    the trail names people."""
    assert viewer_client.get("/api/v1/webhooks").status_code == 403
    assert _create(viewer_client).status_code == 403


def test_creating_a_subscription_is_itself_audited(admin_client: httpx.Client) -> None:
    created = _create(admin_client).json()
    entries = admin_client.get("/api/v1/audit-logs?action=webhook.created").json()["items"]
    assert any(entry["entityId"] == created["id"] for entry in entries)
    # The URL is recorded; the secret never is.
    mine = next(entry for entry in entries if entry["entityId"] == created["id"])
    assert created["secret"] not in str(mine)
    admin_client.delete(f"/api/v1/webhooks/{created['id']}")


def test_a_finished_run_announces_itself(completed_run: str) -> None:
    """The event a pipeline waits for, on the stream that carries it.

    This exists because three run events were advertised by the API and
    published by nothing: a subscription to `run.completed` was accepted,
    stored, listed, and never fired. There is nothing to raise when an event
    is simply never produced, so the only way to know is to look for it.

    The stream rather than a receiver: whether a delivery reaches somebody
    else's server is covered by the delivery tests, and putting a real
    endpoint in the middle of this would make it a test about that endpoint.
    """
    import redis

    client = redis.Redis.from_url(
        os.environ.get("PLIMSOLL_TEST_REDIS_URL", "redis://localhost:6379/0")
    )
    try:
        raw = client.xrevrange("webhooks.deliveries", count=200)
    finally:
        client.close()

    # Normalised once. The client is typed as though every field might be
    # bytes, str or absent, and threading that through the assertions would
    # bury what they are actually about.
    entries: list[dict[str, str]] = []
    for _, fields in cast("list[tuple[Any, dict[Any, Any]]]", raw):
        entries.append(
            {
                (k.decode() if isinstance(k, bytes) else str(k)): (
                    v.decode() if isinstance(v, bytes) else str(v)
                )
                for k, v in fields.items()
            }
        )

    mine = {entry["event"]: entry for entry in entries if entry.get("entityId") == completed_run}
    assert "run.completed" in mine, sorted({entry["event"] for entry in entries})
    # A run is not an action somebody took, so nobody is named as its actor.
    assert mine["run.completed"].get("actorId", "") == ""
