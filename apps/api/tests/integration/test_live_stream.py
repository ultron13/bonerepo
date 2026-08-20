"""The dashboard's socket: events while the run is still running."""

import asyncio
import json
import uuid

import httpx
import pytest
import websockets

from tests.integration.conftest import ADMIN, API_URL, VIEWER
from tests.integration.test_run_execution import TERMINAL, _await_status, _short_test

pytestmark = pytest.mark.integration

WS_URL = API_URL.replace("http://", "ws://")


def _stop_and_settle(token: str, run_id: str) -> None:
    """Synchronous on purpose: the client is blocking, so it runs in a thread
    rather than on the loop the socket is using."""
    with httpx.Client(base_url=API_URL, timeout=30) as client:
        client.headers["Authorization"] = f"Bearer {token}"
        client.post(f"/api/v1/runs/{run_id}/stop")
        _await_status(client, run_id, TERMINAL, timeout=300)


def _token(account: dict[str, str]) -> str:
    with httpx.Client(base_url=API_URL, timeout=30) as client:
        return str(client.post("/api/v1/auth/login", json=account).json()["accessToken"])


async def test_an_unauthenticated_socket_never_opens() -> None:
    with pytest.raises(websockets.InvalidStatus):
        async with websockets.connect(f"{WS_URL}/ws/runs/{uuid.uuid4()}"):
            pass


async def test_a_socket_for_an_unknown_run_is_refused() -> None:
    """Refused at the handshake, like an unauthenticated one.

    The run row is the authorisation boundary, and row-level security means
    another organisation's run reads as absent rather than denied -- so the
    socket never opens either way, and the refusal tells an outsider nothing
    about whether the run exists.
    """
    token = await asyncio.to_thread(_token, ADMIN)
    with pytest.raises(websockets.InvalidStatus):
        async with websockets.connect(f"{WS_URL}/ws/runs/{uuid.uuid4()}?token={token}"):
            pass


async def test_a_viewer_may_watch_a_run() -> None:
    """Watching results is a read."""
    token = await asyncio.to_thread(_token, VIEWER)
    admin = await asyncio.to_thread(_token, ADMIN)

    def start() -> str:
        with httpx.Client(base_url=API_URL, timeout=30) as client:
            client.headers["Authorization"] = f"Bearer {admin}"
            test_id = _short_test(client, seconds=20, users=2)
            return str(client.post(f"/api/v1/tests/{test_id}/runs").json()["id"])

    run_id = await asyncio.to_thread(start)
    async with websockets.connect(f"{WS_URL}/ws/runs/{run_id}?token={token}") as socket:
        first = json.loads(await asyncio.wait_for(socket.recv(), timeout=15))
        assert first["type"] == "run.status"

    def finish() -> None:
        with httpx.Client(base_url=API_URL, timeout=30) as client:
            client.headers["Authorization"] = f"Bearer {admin}"
            _await_status(client, run_id, TERMINAL, timeout=300)

    await asyncio.to_thread(finish)


async def test_windows_arrive_while_the_run_is_still_running() -> None:
    """The point of streaming, asserted where it cannot pass after the fact.

    A metric event must land before the run reaches a terminal state; a socket
    that only delivered after completion would be a slower way to read the
    results endpoint.
    """
    token = await asyncio.to_thread(_token, ADMIN)

    def start() -> str:
        with httpx.Client(base_url=API_URL, timeout=30) as client:
            client.headers["Authorization"] = f"Bearer {token}"
            test_id = _short_test(client, seconds=45, users=4)
            return str(client.post(f"/api/v1/tests/{test_id}/runs").json()["id"])

    def status(run_id: str) -> str:
        with httpx.Client(base_url=API_URL, timeout=30) as client:
            client.headers["Authorization"] = f"Bearer {token}"
            return str(client.get(f"/api/v1/runs/{run_id}/status").json()["status"])

    run_id = await asyncio.to_thread(start)
    metric = None
    try:
        async with websockets.connect(f"{WS_URL}/ws/runs/{run_id}?token={token}") as socket:
            deadline = asyncio.get_running_loop().time() + 200
            while asyncio.get_running_loop().time() < deadline:
                try:
                    event = json.loads(await asyncio.wait_for(socket.recv(), timeout=20))
                except TimeoutError:
                    continue
                if event["type"] == "metric":
                    metric = event
                    break
                if event.get("status") in TERMINAL:
                    break
    finally:
        await asyncio.to_thread(_stop_and_settle, token, run_id)

    assert metric is not None, "no metric event arrived over the socket"
    assert metric["transaction"]
    assert int(metric["count"]) > 0
    # Derived at push time from that window's merged sketch, not carried.
    assert int(metric["p95"]) >= int(metric["p50"])
    assert status(run_id) in TERMINAL


async def test_the_stream_and_the_results_endpoint_agree() -> None:
    """A window can be announced more than once.

    Samples belonging to it may be read after it was drained, so the worker
    merges and re-announces. Each announcement therefore carries the running
    total for that window, not the increment -- a subscriber keyed on
    (transaction, window) and taking the latest must end up with the number the
    results endpoint reports, or the dashboard and the report disagree.
    """
    token = await asyncio.to_thread(_token, ADMIN)

    def start() -> str:
        with httpx.Client(base_url=API_URL, timeout=30) as client:
            client.headers["Authorization"] = f"Bearer {token}"
            test_id = _short_test(client, seconds=30, users=4)
            return str(client.post(f"/api/v1/tests/{test_id}/runs").json()["id"])

    def summary(run_id: str) -> dict[str, int]:
        with httpx.Client(base_url=API_URL, timeout=30) as client:
            client.headers["Authorization"] = f"Bearer {token}"
            _await_status(client, run_id, TERMINAL, timeout=300)
            body = client.get(f"/api/v1/runs/{run_id}/metrics").json()
            return {item["transaction"]: item["count"] for item in body["transactions"]}

    run_id = await asyncio.to_thread(start)
    latest: dict[tuple[str, str], int] = {}
    async with websockets.connect(f"{WS_URL}/ws/runs/{run_id}?token={token}") as socket:
        deadline = asyncio.get_running_loop().time() + 200
        while asyncio.get_running_loop().time() < deadline:
            try:
                event = json.loads(await asyncio.wait_for(socket.recv(), timeout=25))
            except TimeoutError:
                break
            if event["type"] == "metric":
                key = (event["transaction"], event["windowStart"])
                count = int(event["count"])
                # A window never goes backwards: it is a total, not a delta.
                assert count >= latest.get(key, 0), (key, latest.get(key), count)
                latest[key] = count
            if event.get("status") in TERMINAL:
                break

    counts = await asyncio.to_thread(summary, run_id)
    streamed: dict[str, int] = {}
    for (transaction, _), count in latest.items():
        streamed[transaction] = streamed.get(transaction, 0) + count

    assert streamed, "nothing was streamed"
    for transaction, total in streamed.items():
        assert counts.get(transaction) == total, (transaction, counts.get(transaction), total)
