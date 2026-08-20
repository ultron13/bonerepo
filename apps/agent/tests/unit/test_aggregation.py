"""Samples in, one sketch per transaction per window out."""

from datetime import UTC, datetime

from plimsoll_agent.aggregation import Folder
from plimsoll_agent.jtl import Sample
from plimsoll_contracts.metrics import decode_sketch, percentile

BASE = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _sample(offset: float, label: str = "Browse", elapsed: int = 100, ok: bool = True) -> Sample:
    return Sample(
        at=BASE.timestamp() + offset,
        label=label,
        elapsed=elapsed,
        success=ok,
        response_code="200" if ok else "500",
        message="" if ok else "Internal Server Error",
    )


def test_samples_in_one_window_become_one_sketch() -> None:
    folder = Folder(run_id="r", ordinal=0)
    for offset in (0.0, 1.0, 2.0, 3.0, 4.0):
        folder.record(_sample(offset))

    windows = folder.drain(BASE.timestamp() + 30)
    assert len(windows) == 1
    assert windows[0].count == 5
    assert windows[0].transaction == "Browse"


def test_a_new_window_starts_every_five_seconds() -> None:
    folder = Folder(run_id="r", ordinal=0)
    folder.record(_sample(1.0))
    folder.record(_sample(7.0))

    windows = folder.drain(BASE.timestamp() + 30)
    assert len(windows) == 2
    assert {w.count for w in windows} == {1}


def test_transactions_are_kept_apart() -> None:
    """Merging Browse into Checkout would make both numbers meaningless."""
    folder = Folder(run_id="r", ordinal=0)
    folder.record(_sample(0.0, label="Browse", elapsed=100))
    folder.record(_sample(0.0, label="Checkout", elapsed=900))

    windows = {w.transaction: w for w in folder.drain(BASE.timestamp() + 30)}
    assert set(windows) == {"Browse", "Checkout"}
    assert percentile(decode_sketch(windows["Checkout"].sketch), 50) >= 890


def test_errors_are_counted_but_still_timed() -> None:
    """A failed request still took time, and hiding it flatters the result."""
    folder = Folder(run_id="r", ordinal=0)
    folder.record(_sample(0.0, ok=True))
    folder.record(_sample(1.0, ok=False, elapsed=3000))

    window = folder.drain(BASE.timestamp() + 30)[0]
    assert window.count == 2
    assert window.error_count == 1
    assert window.maximum >= 3000


def test_an_open_window_is_not_drained_early() -> None:
    """Draining a window still being written would ship half of it and then
    ship the other half as a second window with the same key."""
    folder = Folder(run_id="r", ordinal=0)
    folder.record(_sample(0.0))
    assert folder.drain(BASE.timestamp() + 1) == []


def test_draining_twice_does_not_repeat_a_window() -> None:
    folder = Folder(run_id="r", ordinal=0)
    folder.record(_sample(0.0))
    assert len(folder.drain(BASE.timestamp() + 30)) == 1
    assert folder.drain(BASE.timestamp() + 30) == []


def test_a_final_drain_takes_the_open_window_too() -> None:
    """The last window of a run has not closed when JMeter stops, and losing
    it would silently drop the end of every test."""
    folder = Folder(run_id="r", ordinal=0)
    folder.record(_sample(0.0))
    assert len(folder.drain(float("inf"))) == 1
