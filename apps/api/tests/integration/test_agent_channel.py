"""The agent's only door into the control plane.

A generator runs a user-supplied plan, so what this endpoint refuses matters as
much as what it accepts.
"""

import asyncio
import json
import uuid
from typing import Any

import httpx
import pytest
import websockets

from plimsoll_api.security.tokens import issue_agent_token
from plimsoll_api.seed import DEMO_TEST_ID

pytestmark = pytest.mark.integration

WS_URL = "ws://localhost:8000/api/v1/agent/runs"


async def _run_id(client: httpx.Client) -> uuid.UUID:
    """The fixture's client is synchronous, so every call it makes from an
    async test runs off the loop the WebSocket is waiting on."""
    response = await asyncio.to_thread(client.post, f"/api/v1/tests/{DEMO_TEST_ID}/runs")
    return uuid.UUID(response.json()["id"])


async def _status(client: httpx.Client, run_id: uuid.UUID) -> Any:
    response = await asyncio.to_thread(client.get, f"/api/v1/runs/{run_id}/status")
    return response.json()


async def _open(run_id: uuid.UUID, token: str) -> websockets.ClientConnection:
    return await websockets.connect(
        f"{WS_URL}/{run_id}", additional_headers={"Authorization": f"Bearer {token}"}
    )


async def test_an_agent_registers_and_is_acknowledged(
    admin_client: httpx.Client, admin_org: uuid.UUID
) -> None:
    run_id = await _run_id(admin_client)
    token = issue_agent_token(run_id, ordinal=0, org_id=admin_org, ttl_seconds=300)

    async with await _open(run_id, token) as socket:
        await socket.send(json.dumps({"type": "register", "ordinal": 0, "version": "0.1.0"}))
        acknowledgement = json.loads(await socket.recv())

    assert acknowledgement["type"] == "registered"
    assert acknowledgement["desiredState"] in {"QUEUED", "ALLOCATING", "STARTING"}


async def test_a_heartbeat_is_answered_with_the_desired_state(
    admin_client: httpx.Client, admin_org: uuid.UUID
) -> None:
    run_id = await _run_id(admin_client)
    token = issue_agent_token(run_id, ordinal=0, org_id=admin_org, ttl_seconds=300)

    async with await _open(run_id, token) as socket:
        await socket.send(json.dumps({"type": "register", "ordinal": 0, "version": "0.1.0"}))
        await socket.recv()
        await socket.send(json.dumps({"type": "heartbeat"}))
        beat = json.loads(await socket.recv())

    assert beat["type"] == "heartbeat_ack"
    assert "desiredState" in beat

    status = await _status(admin_client, run_id)
    assert status["generators"][0]["lastHeartbeat"] is not None


async def test_a_token_for_another_run_is_refused(
    admin_client: httpx.Client, admin_org: uuid.UUID
) -> None:
    """The token names one run; the path names another."""
    run_id = await _run_id(admin_client)
    token = issue_agent_token(uuid.uuid4(), ordinal=0, org_id=admin_org, ttl_seconds=300)

    with pytest.raises(websockets.exceptions.InvalidStatus):
        async with await _open(run_id, token):
            pass


async def test_an_ordinary_access_token_is_refused(
    admin_client: httpx.Client, admin_org: uuid.UUID
) -> None:
    """An access token has no `aud: agent`, and this door takes nothing else."""
    from plimsoll_api.security.tokens import issue_access_token

    run_id = await _run_id(admin_client)
    token = issue_access_token(uuid.uuid4(), admin_org, "ORG_ADMIN")

    with pytest.raises(websockets.exceptions.InvalidStatus):
        async with await _open(run_id, token):
            pass


async def test_a_state_report_lands_on_the_generator_row(
    admin_client: httpx.Client, admin_org: uuid.UUID
) -> None:
    run_id = await _run_id(admin_client)
    token = issue_agent_token(run_id, ordinal=0, org_id=admin_org, ttl_seconds=300)

    async with await _open(run_id, token) as socket:
        await socket.send(json.dumps({"type": "register", "ordinal": 0, "version": "0.1.0"}))
        await socket.recv()
        await socket.send(json.dumps({"type": "state", "state": "READY", "reason": None}))
        await socket.recv()

    status = await _status(admin_client, run_id)
    assert status["generators"][0]["status"] == "READY"
