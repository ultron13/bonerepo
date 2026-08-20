"""Numbers about Plimsoll itself.

The platform exists to measure other systems. An operator running it needs the
same three answers about it: is it serving, is the work getting done, and when
did it last do any. Everything here is chosen because an alert would be built
on it, not because it was easy to count.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

REQUESTS = Counter(
    "plimsoll_http_requests_total",
    "Requests served, by route and outcome.",
    ["method", "route", "status"],
)
LATENCY = Histogram(
    "plimsoll_http_request_seconds",
    "How long a request took, by route.",
    ["method", "route"],
    # Tuned to a control plane, not a load generator: the interesting failures
    # here are a slow query and a hung upstream, both well above a millisecond.
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
AUTH_FAILURES = Counter(
    "plimsoll_auth_failures_total",
    "Sign-in attempts refused, by reason.",
    ["reason"],
)

WORKER_TICKS = Counter(
    "plimsoll_worker_ticks_total",
    "Reconciliation passes completed.",
)
WORKER_DECISIONS = Counter(
    "plimsoll_worker_decisions_total",
    "What the reconciler decided, by decision.",
    ["decision"],
)
WORKER_LAST_TICK = Gauge(
    "plimsoll_worker_last_tick_timestamp",
    "When the reconciler last completed a pass, in seconds since the epoch.",
)
WORKER_FAILURES = Counter(
    "plimsoll_worker_failures_total",
    "Reconciliations that raised, by stage.",
    ["stage"],
)
METRIC_WINDOWS = Counter(
    "plimsoll_metric_windows_total",
    "Merged metric windows written.",
)


_last_tick = 0.0


def mark_tick() -> None:
    """One reconciliation pass completed.

    Recorded twice on purpose: the gauge is what a scraper reads, and the local
    value is what the worker's own liveness probe reads, which must not depend
    on parsing its own metrics output.
    """
    global _last_tick
    _last_tick = time.time()
    WORKER_TICKS.inc()
    WORKER_LAST_TICK.set(_last_tick)


def seconds_since_tick() -> float | None:
    """None until the first pass: a worker that has not finished one yet is
    starting up, not stalled."""
    return None if _last_tick == 0.0 else time.time() - _last_tick


def render() -> tuple[bytes, str]:
    return generate_latest(), CONTENT_TYPE_LATEST


async def measure(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Count by route template rather than by path.

    A label per run identifier would give the metric one series per run and
    make the store useless within a day -- the cardinality problem that turns
    monitoring into an outage of its own.
    """
    started = time.perf_counter()
    response = await call_next(request)
    route = request.scope.get("route")
    template = getattr(route, "path", None) or "unmatched"
    method = request.method

    REQUESTS.labels(method=method, route=template, status=str(response.status_code)).inc()
    LATENCY.labels(method=method, route=template).observe(time.perf_counter() - started)
    return response
