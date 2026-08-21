"""Every probe path in the Helm chart is a path the application answers.

This exists because the opposite shipped. The chart asked Kubernetes to poll
`/api/v1/health/ready` and `/api/v1/health/live`; the API serves `/readyz` and
`/healthz`. On a real cluster the readiness probe would never have succeeded,
so the Service would have had no endpoints and the deployment would never have
served anything -- while the liveness probe restarted the pods into a crash
loop.

The chart had been checked against a live API server and passed, because
`--dry-run=server` and an install whose images cannot be pulled validate the
shape of a manifest and never run a probe. Structure was verified; behaviour
was not, and probes are behaviour.

Reading the chart rather than restating it: a test with its own copy of the
paths would agree with itself while the chart was wrong.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from plimsoll_api.main import create_app

CHART = pathlib.Path(__file__).resolve().parents[4] / "infrastructure" / "kubernetes" / "plimsoll"
PROBE = re.compile(r"httpGet:\s*\{\s*path:\s*(?P<path>[^,\s]+)")

# The worker serves its own probe on its own port, out of this application.
# Named here so it is skipped deliberately rather than by not being noticed.
NOT_THE_API = {"worker.yaml", "web.yaml"}


def _chart_probe_paths() -> list[tuple[str, str]]:
    found = []
    for template in sorted((CHART / "templates").glob("*.yaml")):
        if template.name in NOT_THE_API:
            continue
        for match in PROBE.finditer(template.read_text()):
            found.append((template.name, match.group("path")))
    return found


def test_the_chart_has_probes_to_check() -> None:
    """The premise. A regex that matched nothing would make the test below
    vacuous, and it would stay vacuous through any rename."""
    assert _chart_probe_paths(), f"no probes found under {CHART}"


@pytest.mark.parametrize(("template", "path"), _chart_probe_paths())
def test_a_probe_path_is_one_the_api_answers(template: str, path: str) -> None:
    """Asked by making the request, not by reading the route table.

    The first version of this walked `app.routes` and reported that /healthz
    was not served, which was wrong: this FastAPI wraps included routers in
    objects that do not expose a path, and the health routes are
    `include_in_schema=False` so they are not in the OpenAPI document either.
    A probe is a request, so the test is a request -- and it does not depend
    on the shape of FastAPI's internals, which is what made the first attempt
    disagree with a working system.

    Any answer but 404 passes. 503 means the path exists and a dependency is
    unhappy, which is a probe doing its job; 404 means Kubernetes is polling
    something nobody serves.
    """
    from fastapi.testclient import TestClient

    with TestClient(create_app(), raise_server_exceptions=False) as client:
        response = client.get(path)
    assert response.status_code != 404, (
        f"{template} probes {path}; the application answers 404 there. "
        "A readiness probe that never succeeds means a Service with no endpoints, "
        "and a liveness probe that never succeeds means a crash loop."
    )


def test_the_worker_is_probed_where_it_listens() -> None:
    """Different application, same failure if it drifts. Read from the
    worker's own source rather than restated here."""
    from plimsoll_worker import serving

    chart = (CHART / "templates" / "worker.yaml").read_text()
    probed = PROBE.search(chart)
    assert probed is not None, "the worker deployment has no probe"
    served = pathlib.Path(serving.__file__).read_text()
    assert f'"{probed.group("path")}"' in served, (
        f"the worker is probed at {probed.group('path')}, which it does not serve"
    )
