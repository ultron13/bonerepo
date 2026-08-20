"""plimsoll-agent.

Register, fetch the staged bundle, refuse a target the policy does not permit,
run JMeter, upload what it produced, and report terminal state.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx

from plimsoll_agent.bundle import BundleError, download
from plimsoll_agent.channel import VERSION, Channel, command_of, connect
from plimsoll_agent.execution import execute
from plimsoll_agent.lifecycle import Action, next_action
from plimsoll_agent.targets import TargetRefused, hosts_in, refuse_disallowed
from plimsoll_contracts.agent import AgentState, Command, Register
from plimsoll_executor.base import ExecutionContext, Outcome

WORK = Path("/tmp/run")  # noqa: S108 - the container's own tmpfs, not shared
OUTPUT = Path("/tmp/out")  # noqa: S108
UPLOAD_ATTEMPTS = 3


def _files_in(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir() if path.is_file())


async def upload_artifacts(channel: Channel, directory: Path) -> list[str]:
    """A failed upload is a warning, not a lost run.

    The run happened; refusing to record it because a log did not transfer
    would destroy more than it protects.

    A JTL can be large, so both the listing and each read cross into a thread:
    reading one on the loop would stall the heartbeat that keeps this generator
    from being declared lost while it is uploading.
    """
    uploaded: list[str] = []
    for path in await asyncio.to_thread(_files_in, directory):
        await channel.send_raw({"type": "artifact_url_request", "name": path.name})
        reply = await channel.receive()
        if reply.get("type") != "artifact_url":
            continue
        payload = await asyncio.to_thread(path.read_bytes)
        for attempt in range(UPLOAD_ATTEMPTS):
            try:
                async with httpx.AsyncClient(timeout=300) as client:
                    response = await client.put(reply["url"], content=payload)
                response.raise_for_status()
                uploaded.append(path.name)
                break
            except httpx.HTTPError:
                if attempt == UPLOAD_ATTEMPTS - 1:
                    await channel.report(
                        AgentState.RUNNING, f"artifact {path.name} could not be uploaded"
                    )
                else:
                    await asyncio.sleep(2**attempt)
    return uploaded


async def _watch_for_stop(channel: Channel, stop: asyncio.Event) -> None:
    """A stop that arrives mid-run has to be obeyed, so the socket is read
    while JMeter runs rather than slept on."""
    while True:
        message = await channel.receive()
        command = command_of(message)
        if command is None:
            continue
        if next_action(command, AgentState.RUNNING) in (Action.WIND_DOWN, Action.ABANDON):
            stop.set()
            return


async def _prepare(channel: Channel, registered: dict[str, object]) -> tuple[Path, dict[str, str]]:
    """Fetch the bundle and refuse anything the policy does not permit.

    Both happen before a single request leaves this container.
    """
    await channel.report(AgentState.FETCHING)
    root = await asyncio.to_thread(
        download,
        str(registered["bundleUrl"]),
        sha256=str(registered["bundleSha256"]),
        into=WORK,
    )
    plan_file = root / str(registered["planPath"])
    variables = {str(k): str(v) for k, v in dict(registered["variables"]).items()}  # type: ignore[call-overload]

    # Invariant 8's second gate. The hosts are read out of the plan about to be
    # executed, not from a list something else recorded earlier.
    plan_xml = await asyncio.to_thread(plan_file.read_text, errors="replace")
    refuse_disallowed(
        hosts_in(plan_xml),
        allowlist=[str(entry) for entry in list(registered["allowlist"])],  # type: ignore[call-overload]
        variables=variables,
    )
    return plan_file, variables


async def run_agent() -> int:
    api_url = os.environ["PLIMSOLL_API_URL"]
    run_id = os.environ["PLIMSOLL_RUN_ID"]
    token = os.environ["PLIMSOLL_RUN_TOKEN"]
    ordinal = int(os.environ["PLIMSOLL_ORDINAL"])

    async with connect(api_url, run_id, token) as channel:
        await channel.send(Register(ordinal=ordinal, version=VERSION))
        registered = await channel.receive()

        try:
            plan_file, variables = await _prepare(channel, registered)
        except (BundleError, TargetRefused) as exc:
            # Refused before any traffic is generated, and said out loud: an
            # operator has to know which target or which digest was wrong.
            await channel.report(AgentState.FAILED, str(exc))
            return 1

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
                    stop = asyncio.Event()
                    watcher = asyncio.create_task(_watch_for_stop(channel, stop))
                    try:
                        outcome = await execute(
                            ExecutionContext(
                                plan_path=plan_file,
                                working_directory=plan_file.parent,
                                output_directory=OUTPUT,
                                threads=int(str(registered["assignedUsers"])),
                                ramp_up_seconds=int(str(registered["rampUpSeconds"])),
                                duration_seconds=int(str(registered["durationSeconds"])),
                                variables=variables,
                            ),
                            stop,
                        )
                    finally:
                        watcher.cancel()
                    await upload_artifacts(channel, OUTPUT)
                    state = (
                        AgentState.COMPLETED if outcome is Outcome.COMPLETED else AgentState.FAILED
                    )
                    await channel.report(state)
                    return 0 if outcome is Outcome.COMPLETED else 1
                if action in (Action.FINISH, Action.ABANDON):
                    state = AgentState.COMPLETED
                    await channel.report(state, "stopped before starting")
                    return 0
        finally:
            beats.cancel()


def main() -> int:
    if os.environ.get("PLIMSOLL_SLEEP_FOREVER"):
        # A generator with no run to join, used by the runtime's own tests.
        asyncio.run(asyncio.sleep(3600))
        return 0
    return asyncio.run(run_agent())


if __name__ == "__main__":
    sys.exit(main())
