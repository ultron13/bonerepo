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


def test_failures_reach_the_api_as_counted_groups(admin_client: httpx.Client) -> None:
    """The pipeline, end to end, asserted on what is deterministic.

    That grouping compresses is proven where it can be: `test_errors.py` folds
    fifty identical failures into one group with a count of fifty. Repeating
    that here would depend on the demo target's one-percent failure rate
    producing two of the *same* fault in one short run -- two coin flips, and a
    test that fails for reasons unrelated to the code.

    What this asserts instead holds every time: failures reach the API, they
    arrive as groups whose counts add up to the total, and they collapse into
    the handful of faults the plan can actually produce rather than one row per
    occurrence.
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
    # Counts are real, not placeholders.
    assert sum(int(group["count"]) for group in groups) == total, groups
    # The plan drives three transactions, so three faults is the ceiling. One
    # row per occurrence would pass this only while failures were vanishingly
    # rare, and blow past it the moment they were not.
    assert len(groups) <= 3, groups


def test_a_viewer_may_read_errors(viewer_client: httpx.Client, completed_run: str) -> None:
    assert viewer_client.get(f"/api/v1/runs/{completed_run}/errors").status_code == 200
