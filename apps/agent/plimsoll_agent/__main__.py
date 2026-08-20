"""plimsoll-agent.

Register, fetch the staged bundle, refuse a target the policy does not permit,
run JMeter, upload what it produced, and report terminal state.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from plimsoll_agent.aggregation import Folder
from plimsoll_agent.bundle import BundleError, download
from plimsoll_agent.channel import VERSION, Channel, command_of, connect
from plimsoll_agent.errors import ErrorFolder
from plimsoll_agent.execution import execute
from plimsoll_agent.jtl import JtlReader
from plimsoll_agent.lifecycle import Action, next_action
from plimsoll_agent.targets import TargetRefused, hosts_in, refuse_disallowed
from plimsoll_contracts.agent import AgentState, Command, Register
from plimsoll_contracts.metrics import WINDOW_SECONDS, SketchWindow
from plimsoll_executor.base import ExecutionContext, Outcome

WORK = Path("/tmp/run")  # noqa: S108 - the container's own tmpfs, not shared
OUTPUT = Path("/tmp/out")  # noqa: S108
UPLOAD_ATTEMPTS = 3
RESULTS_NAME = "results.jtl"


# A reply may sit behind frames this socket did not ask for -- a heartbeat
# acknowledgement, a command push, the receipt for a metrics frame. Reading a
# fixed number ahead is what stops one of those being mistaken for the answer.
REPLY_LOOKAHEAD = 20


async def _await_reply(channel: Channel, kind: str, name: str) -> dict[str, Any] | None:
    """The next frame is not necessarily the answer to the last question."""
    for _ in range(REPLY_LOOKAHEAD):
        message = await channel.receive()
        if message.get("type") == kind and message.get("name") == name:
            return message
    return None


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
        reply = await _await_reply(channel, "artifact_url", path.name)
        if reply is None:
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


def _read_more(handle: object) -> str:
    text: str = handle.read()  # type: ignore[attr-defined]
    return text


async def _ship_errors(channel: Channel, groups: list[dict[str, str]]) -> None:
    """Grouped failures, on the same terms as the windows: losing them is
    degraded reporting, never a failed run."""
    if not groups:
        return
    with contextlib.suppress(Exception):
        await channel.send_raw({"type": "errors", "groups": groups})


async def _ship_metrics(channel: Channel, windows: list[SketchWindow]) -> None:
    """A metrics failure never fails a run.

    The run generated the load either way; refusing to record that because a
    window did not transfer would destroy more than it protects.
    """
    if not windows:
        return
    with contextlib.suppress(Exception):
        await channel.send_raw({"type": "metrics", "windows": [w.as_message() for w in windows]})


async def _fold_while_running(
    channel: Channel,
    folder: Folder,
    faults: ErrorFolder,
    results: Path,
    finished: asyncio.Event,
) -> None:
    """Tail the JTL beside JMeter and ship each window as it closes.

    Reading the file JMeter is writing is what makes the dashboard live; the
    same bytes reach object storage at the end regardless, so nothing here is
    the only copy of anything.
    """
    reader = JtlReader()
    handle = None
    try:
        while True:
            if handle is None and await asyncio.to_thread(results.exists):
                handle = await asyncio.to_thread(results.open, "r")
            if handle is not None:
                text = await asyncio.to_thread(_read_more, handle)
                for sample in reader.feed(text):
                    folder.record(sample)
                    faults.record(sample)
                await _ship_metrics(channel, folder.drain(time.time()))
                await _ship_errors(channel, faults.drain())
            if finished.is_set():
                # One last pass, then close the window JMeter was still filling
                # when it stopped -- otherwise the end of every run is lost.
                if handle is not None:
                    for sample in reader.feed(await asyncio.to_thread(_read_more, handle)):
                        folder.record(sample)
                        faults.record(sample)
                await _ship_metrics(channel, folder.drain_all())
                await _ship_errors(channel, faults.drain())
                return
            await asyncio.wait([asyncio.create_task(finished.wait())], timeout=WINDOW_SECONDS)
    finally:
        if handle is not None:
            await asyncio.to_thread(handle.close)


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
                    finished = asyncio.Event()
                    folding = asyncio.create_task(
                        _fold_while_running(
                            channel,
                            Folder(run_id=run_id, ordinal=ordinal),
                            ErrorFolder(),
                            OUTPUT / RESULTS_NAME,
                            finished,
                        )
                    )
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
                        # The folder gets to finish: its last drain carries the
                        # window JMeter was still filling.
                        finished.set()
                        with contextlib.suppress(Exception):
                            await asyncio.wait_for(folding, timeout=WINDOW_SECONDS * 2)
                        folding.cancel()
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
