"""Running the engine, and winding it down when asked."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal

from plimsoll_executor.base import ExecutionContext, Outcome
from plimsoll_executor.jmeter import JMeterExecutor

# The heap is sized from the container rather than fixed, because the container
# is sized by the pool. A fixed -Xmx larger than the limit is killed the moment
# the heap grows into it, and one smaller wastes what the operator paid for.
# Modern JVMs read the cgroup limit, so a percentage tracks whatever the pool
# was configured with -- and 75% leaves room for JMeter's own non-heap memory,
# the sample buffer, and the thread stacks a load generator is full of.
DEFAULT_JVM_ARGS = "-XX:InitialRAMPercentage=40 -XX:MaxRAMPercentage=75"


async def execute(context: ExecutionContext, stop: asyncio.Event) -> Outcome:
    """Run the engine, and wind it down on request.

    A stop is a graceful shutdown: JMeter is asked to finish, and its results
    are kept. Killing it would leave a truncated JTL and lose the run.
    """
    executor = JMeterExecutor()
    context.output_directory.mkdir(parents=True, exist_ok=True)
    process = await asyncio.create_subprocess_exec(
        *executor.command(context),
        cwd=str(context.working_directory),
        # JMeter writes its own log through -j, which is uploaded as an
        # artifact. A pipe nothing drains fills and wedges the engine, so
        # neither stream is piped here.
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        # `bin/jmeter` is a shell wrapper that forks the JVM rather than
        # exec'ing it, so signalling the process we spawned can leave the JVM
        # running and reparented. Its own session makes the whole tree
        # addressable, and the group is what gets signalled below.
        start_new_session=True,
        # The resolved variables reach the engine on argv inside this
        # container, and are never written to disk.
        env={**os.environ, "JVM_ARGS": os.environ.get("JVM_ARGS", DEFAULT_JVM_ARGS)},
    )

    async def wind_down() -> None:
        await stop.wait()
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)

    watcher = asyncio.create_task(wind_down())
    try:
        await process.wait()
    finally:
        watcher.cancel()

    if stop.is_set():
        # The engine exits non-zero because we signalled it, and an exit code
        # that reports our own request back to us says nothing about the run.
        # A stop keeps what it measured -- that is the whole difference between
        # stop and cancel.
        return Outcome.COMPLETED
    return executor.interpret(process.returncode or 0)
