"""The dashboard's socket: one run's events, as they happen.

A user's socket, not an agent's. It carries an ordinary access token and is
checked against the ordinary permissions, which is what keeps the two token
families -- and the two trust boundaries -- apart.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from plimsoll_api.db.session import session_for_org
from plimsoll_api.messaging import get_bus, live_channel
from plimsoll_api.repositories import runs as repo
from plimsoll_api.security.permissions import Permission, permissions_for
from plimsoll_api.security.tokens import AccessClaims, TokenError, decode_access_token
from plimsoll_contracts.runs import TERMINAL_RUN_STATUSES

# How often the run's own status is re-read while a client is watching. The
# metrics arrive by announcement; this is what notices the run ending, which
# nothing announces.
STATUS_POLL_SECONDS = 2


def _claims(websocket: WebSocket) -> AccessClaims | None:
    """A browser cannot set a header on a WebSocket, so the token may arrive as
    a query parameter. It is the same token, checked the same way."""
    header = websocket.headers.get("authorization", "")
    raw = (
        header.removeprefix("Bearer ")
        if header.startswith("Bearer ")
        else websocket.query_params.get("token", "")
    )
    if not raw:
        return None
    try:
        claims = decode_access_token(raw)
    except TokenError:
        return None
    if Permission.TEST_READ not in permissions_for(claims.role):
        return None
    return claims


router = APIRouter()


@router.websocket("/ws/runs/{run_id}")
async def run_events(websocket: WebSocket, run_id: uuid.UUID) -> None:
    claims = _claims(websocket)
    if claims is None:
        # Refused before the upgrade: an unauthenticated socket never opens.
        await websocket.close(code=4401)
        return

    async with session_for_org(claims.organization_id) as session:
        run = await repo.get(session, run_id)
    if run is None:
        # The run row is the authorisation boundary, and row-level security
        # means another organisation's run reads as absent rather than denied.
        await websocket.close(code=4404)
        return

    await websocket.accept()
    await websocket.send_text(
        json.dumps({"type": "run.status", "runId": str(run_id), "status": run.status})
    )

    async def watch_status() -> None:
        """Announce the ending, which no publisher does."""
        last = run.status
        while True:
            await asyncio.sleep(STATUS_POLL_SECONDS)
            async with session_for_org(claims.organization_id) as session:
                current = await repo.get(session, run_id)
            if current is None:
                return
            if current.status != last:
                last = current.status
                await websocket.send_text(
                    json.dumps(
                        {"type": "run.status", "runId": str(run_id), "status": current.status}
                    )
                )
            if current.status in TERMINAL_RUN_STATUSES:
                return

    watcher = asyncio.create_task(watch_status())
    try:
        async with get_bus().listen(live_channel(run_id)) as events:
            async for event in events:
                if watcher.done():
                    break
                await websocket.send_text(json.dumps(event))
    except WebSocketDisconnect:
        # A client closing its tab is not a failure.
        pass
    finally:
        watcher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watcher
