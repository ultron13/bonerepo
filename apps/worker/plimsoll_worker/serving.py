"""The worker's own small HTTP surface.

It serves no requests, which is exactly why it needs one: without it, nothing
outside the process can tell a working worker from a wedged one. Kubernetes
needs somewhere to send a liveness probe, and a scraper needs somewhere to find
the number an alert is built on.

Deliberately not FastAPI. This is two endpoints with no routing, no validation,
and no dependency injection, and pulling a framework in for them would put the
control plane's request stack inside the process that talks to a Docker socket.
"""

from __future__ import annotations

import asyncio
import logging

from plimsoll_api.observability import render, seconds_since_tick

logger = logging.getLogger("plimsoll.worker")

# How long a worker may go without completing a pass before a liveness probe
# calls it dead. Generously more than a tick, because a slow reconciliation is
# not a wedged one and restarting mid-provision is the thing to avoid.
LIVENESS_TIMEOUT_SECONDS = 120


def _liveness() -> tuple[int, bytes]:
    since = seconds_since_tick()
    if since is not None and since > LIVENESS_TIMEOUT_SECONDS:
        # Restarting is safe: the reconciler reads its state from the database,
        # and a run abandoned part-way through is adopted by whoever comes next.
        return 503, b"stalled\n"
    return 200, b"ok\n"


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        request = await asyncio.wait_for(reader.readline(), timeout=5)
        target = request.decode("latin-1").split(" ")[1] if b" " in request else "/"

        if target.startswith("/metrics"):
            body, content_type = render()
            status, headers = 200, f"Content-Type: {content_type}"
        elif target.startswith("/healthz"):
            code, body = _liveness()
            status, headers = code, "Content-Type: text/plain"
        else:
            status, body, headers = 404, b"not found\n", "Content-Type: text/plain"

        writer.write(
            f"HTTP/1.1 {status} \r\n{headers}\r\nContent-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n".encode("latin-1")
            + body
        )
        await writer.drain()
    except (TimeoutError, ConnectionError):
        # A probe that hung up is not an event worth logging every interval.
        pass
    finally:
        writer.close()


async def serve(port: int, stopping: asyncio.Event) -> None:
    server = await asyncio.start_server(_handle, host="0.0.0.0", port=port)  # noqa: S104
    logger.info("worker listening on :%d for metrics and health", port)
    async with server:
        await stopping.wait()
