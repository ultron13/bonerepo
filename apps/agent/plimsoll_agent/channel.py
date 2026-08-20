"""The socket half: connect, register, heartbeat, and read commands."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import websockets

from plimsoll_contracts.agent import AgentState, Command, Heartbeat, Register, StateReport

HEARTBEAT_SECONDS = 10
VERSION = "0.1.0"


class Channel:
    """One socket, read by one coroutine at a time.

    The heartbeat only ever sends, so it never races the reader: whichever of
    the main loop or the execution wait is current owns `receive`.
    """

    def __init__(self, socket: websockets.ClientConnection) -> None:
        self._socket = socket

    async def send(self, model: Register | StateReport | Heartbeat) -> None:
        await self._socket.send(model.model_dump_json())

    async def send_raw(self, payload: dict[str, Any]) -> None:
        """For frames the agent builds by hand rather than from a model."""
        await self._socket.send(json.dumps(payload))

    async def report(self, state: AgentState, reason: str | None = None) -> None:
        await self.send(StateReport(state=state, reason=reason))

    async def receive(self) -> dict[str, Any]:
        message: dict[str, Any] = json.loads(await self._socket.recv())
        return message

    async def heartbeat_forever(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await self.send(Heartbeat())


@asynccontextmanager
async def connect(api_url: str, run_id: str, token: str) -> AsyncIterator[Channel]:
    url = api_url.replace("http://", "ws://").replace("https://", "wss://")
    async with websockets.connect(
        f"{url}/api/v1/agent/runs/{run_id}",
        additional_headers={"Authorization": f"Bearer {token}"},
        # A generator with a wedged socket is worse than one that reconnects.
        open_timeout=30,
        ping_interval=20,
    ) as socket:
        yield Channel(socket)


def command_of(message: dict[str, Any]) -> Command | None:
    value = message.get("command")
    return Command(value) if value else None
