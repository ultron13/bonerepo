"""Whether a wedged worker admits it.

A worker that stopped reconciling looks exactly like an idle one from outside.
The liveness probe is the only thing that distinguishes them, so the case worth
testing is the one that fails.
"""

import plimsoll_api.observability as observability
from plimsoll_worker.serving import LIVENESS_TIMEOUT_SECONDS, _liveness


def test_a_worker_that_has_not_ticked_yet_is_starting_not_stalled(
    monkeypatch: object,
) -> None:
    """Restarting a worker that is still coming up would never let it finish."""
    monkeypatch.setattr(observability, "_last_tick", 0.0)  # type: ignore[attr-defined]
    assert _liveness()[0] == 200


def test_a_worker_that_ticked_recently_is_alive(monkeypatch: object) -> None:
    import time

    monkeypatch.setattr(observability, "_last_tick", time.time() - 1)  # type: ignore[attr-defined]
    assert _liveness()[0] == 200


def test_a_worker_that_stopped_reconciling_reports_unhealthy(monkeypatch: object) -> None:
    """The number an alert is built on, and the restart it triggers.

    Restarting is safe: the reconciler reads its state from the database, and a
    run abandoned part-way through is adopted by whoever comes next.
    """
    import time

    monkeypatch.setattr(  # type: ignore[attr-defined]
        observability, "_last_tick", time.time() - LIVENESS_TIMEOUT_SECONDS - 1
    )
    status, body = _liveness()
    assert status == 503
    assert b"stalled" in body
