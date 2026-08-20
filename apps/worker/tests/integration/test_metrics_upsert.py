"""One row per window, however many times it arrives."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import sqlalchemy as sa

from plimsoll_api.db.session import session_for_org
from plimsoll_contracts.metrics import encode_sketch, from_bytes, new_sketch, percentile
from plimsoll_worker.metrics import merge_batch, write

pytestmark = pytest.mark.integration

ORG = uuid.UUID("00000000-0000-0000-0000-0000000000de")


def _window(run_id: uuid.UUID, values: list[int], transaction: str = "Browse") -> dict[str, str]:
    sketch = new_sketch()
    for value in values:
        sketch.record_value(value)
    return {
        "runId": str(run_id),
        "organizationId": str(ORG),
        "ordinal": "0",
        "transaction": transaction,
        "windowStart": datetime(2026, 8, 20, 12, 0, tzinfo=UTC).isoformat(),
        "count": str(len(values)),
        "errorCount": "0",
        "min": str(min(values)),
        "max": str(max(values)),
        "total": str(sum(values)),
        "sketch": encode_sketch(sketch),
    }


async def _stored(run_id: uuid.UUID) -> list[sa.Row[Any]]:
    async with session_for_org(ORG) as session:
        return list(
            (
                await session.execute(
                    sa.text("SELECT sketch, tags FROM performance_metrics WHERE run_id = :r"),
                    {"r": run_id},
                )
            ).all()
        )


async def test_two_batches_for_one_window_leave_one_row() -> None:
    """A second generator arriving in a later read must widen the window, not
    duplicate it."""
    run_id = uuid.uuid4()
    async with session_for_org(ORG) as session:
        await write(session, merge_batch([_window(run_id, [100] * 10)]))
    async with session_for_org(ORG) as session:
        await write(session, merge_batch([_window(run_id, [500] * 10)]))

    rows = await _stored(run_id)
    assert len(rows) == 1
    assert rows[0].tags["count"] == 20
    assert rows[0].tags["min"] == 100
    assert rows[0].tags["max"] == 500


async def test_the_stored_sketch_holds_both_contributions() -> None:
    """Replacing rather than merging would silently discard a generator."""
    run_id = uuid.uuid4()
    async with session_for_org(ORG) as session:
        await write(session, merge_batch([_window(run_id, [100] * 1000)]))
    async with session_for_org(ORG) as session:
        await write(session, merge_batch([_window(run_id, [5000] * 1000)]))

    merged = from_bytes((await _stored(run_id))[0].sketch)
    assert merged.get_total_count() == 2000
    # Half the samples took 5000ms, so the merged p95 is up there -- which it
    # would not be if the second write had replaced the first.
    assert percentile(merged, 95) >= 4900


async def test_separate_windows_are_still_separate_rows() -> None:
    run_id = uuid.uuid4()
    first = _window(run_id, [100])
    later = _window(run_id, [100])
    later["windowStart"] = datetime(2026, 8, 20, 12, 0, 5, tzinfo=UTC).isoformat()
    async with session_for_org(ORG) as session:
        await write(session, merge_batch([first]))
        await write(session, merge_batch([later]))

    assert len(await _stored(run_id)) == 2
