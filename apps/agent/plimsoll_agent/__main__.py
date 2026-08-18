"""plimsoll-agent.

S3a: register, hold until told to start, wait out the duration, report
COMPLETED. S3b replaces the waiting with JMeter.
"""

from __future__ import annotations

import asyncio
import os
import sys

from plimsoll_agent.channel import VERSION, Channel, command_of, connect
from plimsoll_agent.lifecycle import Action, next_action
from plimsoll_contracts.agent import AgentState, Command, Register


async def _execute(channel: Channel, duration_seconds: int) -> bool:
    """Wait out the duration without going deaf.

    A stop that arrives mid-run has to be obeyed, so the wait is spent reading
    the socket rather than sleeping on it -- otherwise WIND_DOWN could never
    happen and a stop would be honoured only after the run finished anyway.
    Every heartbeat acknowledgement carries the command too, so a stop lands
    within one interval even if its announcement is lost.

    Returns whether it ran to completion rather than being stopped.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + duration_seconds
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            return True
        try:
            message = await asyncio.wait_for(channel.receive(), timeout=remaining)
        except TimeoutError:
            return True
        command = command_of(message)
        if command is None:
            continue
        if next_action(command, AgentState.RUNNING) in (Action.WIND_DOWN, Action.ABANDON):
            return False


async def run_agent() -> int:
    api_url = os.environ["PLIMSOLL_API_URL"]
    run_id = os.environ["PLIMSOLL_RUN_ID"]
    token = os.environ["PLIMSOLL_RUN_TOKEN"]
    ordinal = int(os.environ["PLIMSOLL_ORDINAL"])

    async with connect(api_url, run_id, token) as channel:
        await channel.send(Register(ordinal=ordinal, version=VERSION))
        registered = await channel.receive()
        duration = int(registered["durationSeconds"])

        await channel.report(AgentState.FETCHING)
        await channel.receive()
        # S3b fetches the bundle here.
        state = AgentState.READY
        await channel.report(state)
        await channel.receive()

        beats = asyncio.create_task(channel.heartbeat_forever())
        try:
            while True:
                message = await channel.receive()
                command = command_of(message) or Command.WAIT
                action = next_action(command, state)

                if action is Action.RUN:
                    state = AgentState.RUNNING
                    await channel.report(state)
                    completed = await _execute(channel, duration)
                    state = AgentState.COMPLETED
                    await channel.report(state, None if completed else "stopped")
                    return 0
                if action in (Action.FINISH, Action.ABANDON):
                    state = AgentState.COMPLETED
                    await channel.report(state, "stopped before starting")
                    return 0
        finally:
            beats.cancel()


def main() -> int:
    return asyncio.run(run_agent())


if __name__ == "__main__":
    sys.exit(main())
