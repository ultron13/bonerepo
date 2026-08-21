"""How a delivery behaves when the other end misbehaves.

A real HTTP client against a transport that answers however a test needs, so
the retry decisions, the suspension, and the headers a receiver checks are all
exercised rather than described.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest

from plimsoll_api.security.webhooks import (
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    verify,
)
from plimsoll_worker import webhooks as delivery

SECRET = b"a-shared-secret"
URL = "https://siem.example.com/hooks"
BODY = b'{"event":"user.deactivated"}'


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backoff exists for production, not for a test's patience."""

    async def instantly(seconds: float) -> None:
        return None

    monkeypatch.setattr("plimsoll_worker.webhooks.asyncio.sleep", instantly)


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Answer with a given sequence of statuses, recording what arrived."""
    seen: list[httpx.Request] = []

    def answering(*statuses: int | Exception) -> list[httpx.Request]:
        answers = list(statuses)

        def handle(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            answer = answers.pop(0) if answers else 200
            if isinstance(answer, Exception):
                raise answer
            return httpx.Response(answer)

        real = httpx.AsyncClient

        def build(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
            kwargs["transport"] = httpx.MockTransport(handle)
            return real(*args, **kwargs)

        monkeypatch.setattr("plimsoll_worker.webhooks.httpx.AsyncClient", build)
        return seen

    return answering


@pytest.fixture
def suspensions(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    recorded: list[Any] = []

    async def record(org_id: Any, webhook_id: Any, reason: str) -> None:
        recorded.append((webhook_id, reason))

    monkeypatch.setattr(delivery, "_suspend", record)
    return recorded


@pytest.fixture(autouse=True)
def _resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(delivery, "resolved_targets", lambda url, **kw: ["93.184.216.34"])


async def _deliver(webhook_id: uuid.UUID | None = None) -> None:
    await delivery._deliver_one(uuid.uuid4(), webhook_id or uuid.uuid4(), URL, SECRET, BODY)


async def test_a_delivery_carries_a_signature_a_receiver_can_check(
    transport: Any, suspensions: list[Any]
) -> None:
    """The premise, and the documented procedure: the verify a receiver runs
    is the one this system ships, so it is the one under test."""
    seen = transport(200)
    await _deliver()

    assert len(seen) == 1
    request = seen[0]
    assert verify(
        SECRET,
        request.content,
        request.headers[SIGNATURE_HEADER],
        request.headers[TIMESTAMP_HEADER],
    )
    assert json.loads(request.content)["event"] == "user.deactivated"
    assert suspensions == []


async def test_a_success_is_not_retried(transport: Any, suspensions: list[Any]) -> None:
    seen = transport(204)
    await _deliver()
    assert len(seen) == 1


async def test_a_temporary_failure_is_retried_and_then_succeeds(
    transport: Any, suspensions: list[Any]
) -> None:
    """A receiver restarting should not cost a subscription."""
    seen = transport(503, 200)
    await _deliver()
    assert len(seen) == 2
    assert suspensions == []


async def test_a_refusal_is_not_retried(transport: Any, suspensions: list[Any]) -> None:
    """400 and 401 are the receiver saying it understood and declined.
    Retrying does not improve either, and hammering somebody's endpoint
    because they rejected the first one is how a webhook becomes a nuisance."""
    seen = transport(400)
    await _deliver()
    assert len(seen) == 1
    assert suspensions == []


async def test_an_endpoint_that_never_answers_is_suspended(
    transport: Any, suspensions: list[Any]
) -> None:
    """Left active, a dead endpoint turns every event into a queue of attempts
    that never drains, and the queue is what gets noticed rather than the
    endpoint."""
    seen = transport(503, 503, 503)
    webhook_id = uuid.uuid4()
    await _deliver(webhook_id)

    assert len(seen) == delivery.ATTEMPTS
    assert [entry[0] for entry in suspensions] == [webhook_id]


async def test_a_connection_that_fails_outright_is_retried_then_suspended(
    transport: Any, suspensions: list[Any]
) -> None:
    error = httpx.ConnectError("refused")
    seen = transport(error, error, error)
    await _deliver()
    assert len(seen) == delivery.ATTEMPTS
    assert len(suspensions) == 1


async def test_a_subscription_that_now_points_inside_is_suspended(
    monkeypatch: pytest.MonkeyPatch, transport: Any, suspensions: list[Any]
) -> None:
    """It was acceptable when it was created. DNS is free to answer
    differently since, and a name that was public then and private now is
    exactly how a webhook becomes a way to read this deployment's network."""
    from plimsoll_api.security.webhooks import WebhookRefused

    def refuses(url: str, **kwargs: Any) -> list[str]:
        raise WebhookRefused("resolves to 169.254.169.254")

    monkeypatch.setattr(delivery, "resolved_targets", refuses)
    seen = transport(200)
    await _deliver()

    assert seen == [], "nothing is sent to an address that failed the check"
    assert len(suspensions) == 1


async def test_the_connection_goes_to_the_checked_address_not_the_name(
    transport: Any, suspensions: list[Any]
) -> None:
    """Checking a name and then connecting to it are two different questions
    unless the answer is carried between them. The host header and the TLS
    name stay the hostname, so the receiver still sees what it expects."""
    seen = transport(200)
    await _deliver()

    request = seen[0]
    assert request.url.host == "93.184.216.34"
    assert request.headers["host"] == "siem.example.com"
    assert request.extensions.get("sni_hostname") == "siem.example.com"
