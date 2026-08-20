"""What a finished run measured, answered from merged sketches."""

import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration


def test_an_unknown_run_has_no_results(admin_client: httpx.Client) -> None:
    assert admin_client.get(f"/api/v1/runs/{uuid.uuid4()}/metrics").status_code == 404


def test_a_completed_run_reports_a_transaction_summary(
    admin_client: httpx.Client, completed_run: str
) -> None:
    body = admin_client.get(f"/api/v1/runs/{completed_run}/metrics").json()
    names = {item["transaction"] for item in body["transactions"]}
    assert "Browse" in names, body

    browse = next(item for item in body["transactions"] if item["transaction"] == "Browse")
    assert browse["count"] > 0
    assert browse["p95"] >= browse["p50"]
    assert browse["max"] >= browse["p99"] >= browse["p95"]
    assert browse["min"] <= browse["p50"]


# HDR reports the highest value equivalent to the bucket a sample fell in, so
# a percentile can sit a fraction above the exact maximum -- recording 2500 and
# asking for p99 answers 2501. The architecture states the trade plainly:
# sketches are accurate to about 0.1%, bounded and known, against an averaging
# error that is neither. min and max come from exact counters, so the
# comparison has to allow the sketch its documented precision.
SKETCH_TOLERANCE = 1.002


def test_percentiles_lie_inside_the_observed_range(
    admin_client: httpx.Client, completed_run: str
) -> None:
    """A percentile far outside min..max is the signature of an averaged one.

    Averaging two generators' percentiles produces a number no request saw,
    and it misses by seconds rather than by a rounding step.
    """
    for item in admin_client.get(f"/api/v1/runs/{completed_run}/metrics").json()["transactions"]:
        for name in ("p50", "p90", "p95", "p99"):
            assert item["min"] / SKETCH_TOLERANCE <= item[name], (name, item)
            assert item[name] <= item["max"] * SKETCH_TOLERANCE, (name, item)


def test_the_summary_counts_every_sample_the_windows_hold(
    admin_client: httpx.Client, completed_run: str
) -> None:
    body = admin_client.get(f"/api/v1/runs/{completed_run}/metrics").json()
    assert body["totalSamples"] == sum(item["count"] for item in body["transactions"])
    assert body["totalSamples"] > 0


def test_throughput_is_reported_per_second(admin_client: httpx.Client, completed_run: str) -> None:
    body = admin_client.get(f"/api/v1/runs/{completed_run}/metrics").json()
    browse = next(item for item in body["transactions"] if item["transaction"] == "Browse")
    # The fixture run is 15 seconds; a plausible rate is well under 1000/s and
    # above zero. The point is that it is a rate, not a count.
    assert 0 < browse["throughput"] < 1000
    assert browse["throughput"] < browse["count"]


def test_a_viewer_may_read_results(viewer_client: httpx.Client, completed_run: str) -> None:
    assert viewer_client.get(f"/api/v1/runs/{completed_run}/metrics").status_code == 200


def test_two_generators_report_one_merged_result(admin_client: httpx.Client) -> None:
    """The slice's central claim, end to end.

    A pool narrow enough to force two generators runs one test across both.
    Their windows must merge into one series -- not appear twice, and not be
    averaged -- so the count is the sum and every percentile still lies inside
    the observed range.
    """
    from tests.integration.test_run_execution import TERMINAL, _await_status

    pool_id = admin_client.post(
        "/api/v1/generator-pools",
        json={
            "name": f"narrow-{uuid.uuid4().hex[:8]}",
            "runtime": "docker",
            "config": {"image": "ghcr.io/ultron13/generator:dev"},
            "maxGenerators": 2,
            "maxVusPerGenerator": 2,
        },
    ).json()["id"]

    project_id = admin_client.post(
        "/api/v1/projects",
        json={"name": "Merge", "projectKey": f"G{uuid.uuid4().hex[:8].upper()}"},
    ).json()["id"]
    repo_id = admin_client.post(
        f"/api/v1/projects/{project_id}/script-repos",
        json={
            "name": f"merge-{uuid.uuid4().hex[:6]}",
            "repoUrl": "http://script-fixture/public/plans.git",
            "planPath": "perf/checkout.jmx",
            "defaultRef": "main",
        },
    ).json()["id"]
    test_id = admin_client.post(
        f"/api/v1/projects/{project_id}/tests",
        json={
            "name": "Across two generators",
            # Four users over a pool capped at two each: two generators.
            "configuration": {
                "virtualUsers": 4,
                "durationSeconds": 20,
                "rampUpSeconds": 2,
                "generatorPoolId": pool_id,
            },
            "plans": [{"scriptRepoId": repo_id, "virtualUsers": 4, "executionOrder": 1}],
            "slaRules": [],
        },
    ).json()["id"]

    run_id = admin_client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
    status = _await_status(admin_client, run_id, {"RUNNING"}, timeout=300)
    assert len(status["generators"]) == 2, status

    final = _await_status(admin_client, run_id, TERMINAL, timeout=300)
    assert final["status"] == "COMPLETED", final

    body = admin_client.get(f"/api/v1/runs/{run_id}/metrics").json()
    browse = next(item for item in body["transactions"] if item["transaction"] == "Browse")
    # One series, carrying both generators' samples.
    assert browse["count"] > 0
    assert browse["min"] <= browse["p50"] <= browse["p95"] <= browse["max"]
