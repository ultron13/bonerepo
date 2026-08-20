"""Failures, grouped rather than repeated."""

import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration


def test_an_unknown_run_has_no_errors(admin_client: httpx.Client) -> None:
    assert admin_client.get(f"/api/v1/runs/{uuid.uuid4()}/errors").status_code == 404


def test_the_error_total_agrees_with_the_metrics(
    admin_client: httpx.Client, completed_run: str
) -> None:
    """Two independent paths count the same failures: the metric windows carry
    an error count per transaction, and the groups carry occurrences. They come
    from the same samples, so a disagreement means one of them is losing data.
    """
    errors = admin_client.get(f"/api/v1/runs/{completed_run}/errors").json()
    metrics = admin_client.get(f"/api/v1/runs/{completed_run}/metrics").json()
    assert errors["total"] == metrics["totalErrors"], (errors, metrics)


def test_every_group_is_well_formed(admin_client: httpx.Client, completed_run: str) -> None:
    for item in admin_client.get(f"/api/v1/runs/{completed_run}/errors").json()["items"]:
        assert item["count"] >= 1
        assert item["fingerprint"]
        assert item["firstSeen"] <= item["lastSeen"]
        # A count with no example is a number an operator cannot act on.
        assert item["sample"]


def test_failures_group_rather_than_repeat(admin_client: httpx.Client) -> None:
    """The point of grouping, asserted where it cannot pass vacuously.

    The demo target fails a fixed fraction of requests, so a run with enough
    samples reliably produces more failures than distinct faults -- and the
    groups must be fewer than the occurrences they cover.
    """
    from tests.integration.test_run_execution import TERMINAL, _await_status, _short_test

    test_id = _short_test(admin_client, seconds=40, users=6)
    run_id = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
    _await_status(admin_client, run_id, TERMINAL, timeout=300)

    body: dict[str, object] = {}
    for _ in range(15):
        body = admin_client.get(f"/api/v1/runs/{run_id}/errors").json()
        if int(str(body["total"])) > 0:
            break
        _await_status(admin_client, run_id, TERMINAL, timeout=10)

    total = int(str(body["total"]))
    groups = list(body["items"])  # type: ignore[call-overload]
    assert total > 0, "the demo target injects failures; none were recorded"
    assert len(groups) < total, (len(groups), total)


def test_a_viewer_may_read_errors(viewer_client: httpx.Client, completed_run: str) -> None:
    assert viewer_client.get(f"/api/v1/runs/{completed_run}/errors").status_code == 200
