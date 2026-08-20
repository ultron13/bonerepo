"""Whether the platform can be watched.

Plimsoll measures other systems for a living. An operator running it has to be
able to answer the same questions about it: is it serving, is the worker still
reconciling, and when did it last do anything.
"""

import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration

API = "http://localhost:8000"
WORKER = "http://localhost:9100"


def test_the_api_exposes_metrics() -> None:
    body = httpx.get(f"{API}/metrics", timeout=15).text
    assert "plimsoll_http_requests_total" in body
    assert "plimsoll_http_request_seconds" in body


def test_metrics_are_scrapeable_without_a_token() -> None:
    """A scraper is not a user. Requiring a bearer token here means the metrics
    are collected by nobody, and the endpoint carries no tenant data."""
    response = httpx.get(f"{API}/metrics", timeout=15)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")


def test_requests_are_counted_by_outcome() -> None:
    before = _sample(httpx.get(f"{API}/metrics", timeout=15).text, status="401")
    httpx.get(f"{API}/api/v1/runs/00000000-0000-0000-0000-000000000000", timeout=15)
    after = _sample(httpx.get(f"{API}/metrics", timeout=15).text, status="401")
    assert after > before, (before, after)


def test_a_route_is_counted_by_template_not_by_path() -> None:
    """One series per route, not one per run.

    Labelling by path would give this metric a new series for every identifier
    that has ever been fetched, and a monitoring system that falls over is an
    outage of its own.
    """
    httpx.get(f"{API}/api/v1/runs/{uuid.uuid4()}", timeout=15)
    httpx.get(f"{API}/api/v1/runs/{uuid.uuid4()}", timeout=15)

    body = httpx.get(f"{API}/metrics", timeout=15).text
    series = [
        line
        for line in body.splitlines()
        if line.startswith("plimsoll_http_requests_total") and "/api/v1/runs/" in line
    ]
    assert any('route="/api/v1/runs/{run_id}"' in line for line in series), series
    # No identifier ever appears in a label.
    assert not any("-" in line.split("route=")[1].split(",")[0] for line in series), series


def test_the_worker_can_be_scraped() -> None:
    """It has no request to serve, so without this it is a black box: nothing
    outside it can tell a working worker from a wedged one."""
    body = httpx.get(f"{WORKER}/metrics", timeout=15).text
    assert "plimsoll_worker_ticks_total" in body


def test_the_worker_reports_when_it_last_reconciled() -> None:
    """The number an alert is actually built on. A worker that stopped looks
    exactly like an idle one from outside, except for this."""
    body = httpx.get(f"{WORKER}/metrics", timeout=15).text
    assert "plimsoll_worker_last_tick_timestamp" in body


def test_the_worker_answers_a_liveness_probe() -> None:
    assert httpx.get(f"{WORKER}/healthz", timeout=15).status_code == 200


def _sample(body: str, *, status: str) -> float:
    total = 0.0
    for line in body.splitlines():
        if line.startswith("plimsoll_http_requests_total") and f'status="{status}"' in line:
            total += float(line.rsplit(" ", 1)[1])
    return total
