"""Merging sketches across generators, and writing what they say.

This is where invariant 2 lives. Two generators reporting the same window
produce one row whose sketch is the sum of theirs -- never two rows, and never
an average of two percentiles. The ordinal is deliberately absent from the key:
dropping it is the merge.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from hdrh.histogram import HdrHistogram
from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_contracts.metrics import (
    MetricKind,
    decode_sketch,
    from_bytes,
    new_sketch,
    to_bytes,
)

METRIC_NAME = "transaction.duration"
ENTITY_TYPE = "transaction"


@dataclass
class MergedWindow:
    organization_id: uuid.UUID
    run_id: uuid.UUID
    transaction: str
    window_start: str
    count: int
    error_count: int
    minimum: int
    maximum: int
    total: int
    sketch: HdrHistogram


def merge_batch(messages: list[dict[str, str]]) -> list[MergedWindow]:
    """One row per (run, transaction, window), whoever reported it."""
    merged: dict[tuple[str, str, str], MergedWindow] = {}
    for message in messages:
        key = (message["runId"], message["transaction"], message["windowStart"])
        row = merged.get(key)
        if row is None:
            row = merged[key] = MergedWindow(
                organization_id=uuid.UUID(message["organizationId"]),
                run_id=uuid.UUID(message["runId"]),
                transaction=message["transaction"],
                window_start=message["windowStart"],
                count=0,
                error_count=0,
                minimum=int(message["min"]),
                maximum=0,
                total=0,
                sketch=new_sketch(),
            )
        row.sketch.add(decode_sketch(message["sketch"]))
        row.count += int(message["count"])
        row.error_count += int(message["errorCount"])
        row.total += int(message["total"])
        row.minimum = min(row.minimum, int(message["min"]))
        row.maximum = max(row.maximum, int(message["max"]))
    return list(merged.values())


async def write(session: AsyncSession, rows: list[MergedWindow]) -> None:
    """One row per window, merged on conflict.

    Two generators reporting the same window may arrive in different reads, and
    at-least-once delivery means a batch may arrive twice. Both land here, so
    the stored sketch is read, merged with the incoming one, and written back
    inside the same transaction. Replacing would discard the other generator's
    samples; appending is what the unique index now forbids.

    Merging a redelivered batch does over-count, and the alternative -- keying
    on the delivery -- costs a second table. The window this leaves open is a
    worker dying between the write and the acknowledgement, and the reporting
    error it produces is bounded by one batch.
    """
    for row in rows:
        window = datetime.fromisoformat(row.window_start)
        existing = (
            await session.execute(
                sa.text(
                    "SELECT sketch, tags FROM performance_metrics "
                    "WHERE run_id = :run AND entity_id = :entity AND time = :window "
                    "FOR UPDATE"
                ),
                {"run": row.run_id, "entity": row.transaction, "window": window},
            )
        ).first()

        merged = row.sketch
        count, errors, total = row.count, row.error_count, row.total
        minimum, maximum = row.minimum, row.maximum
        if existing is not None:
            merged = new_sketch()
            merged.add(from_bytes(existing.sketch))
            merged.add(row.sketch)
            stored = existing.tags or {}
            count += int(stored.get("count", 0))
            errors += int(stored.get("errorCount", 0))
            total += int(stored.get("total", 0))
            minimum = min(minimum, int(stored.get("min", minimum)))
            maximum = max(maximum, int(stored.get("max", 0)))

        tags = json.dumps(
            {
                "count": count,
                "errorCount": errors,
                "min": minimum,
                "max": maximum,
                "total": total,
            }
        )
        parameters = {
            "window": window,
            "org": row.organization_id,
            "run": row.run_id,
            "name": METRIC_NAME,
            "kind": str(MetricKind.HISTOGRAM),
            "entity_type": ENTITY_TYPE,
            "entity": row.transaction,
            # A histogram row carries a sketch, never a value: exactly one of
            # the two is populated, decided by the kind.
            "value": None,
            "sketch": to_bytes(merged),
            "tags": tags,
        }
        if existing is None:
            await session.execute(
                sa.text(
                    "INSERT INTO performance_metrics "
                    "(time, organization_id, run_id, metric_name, metric_kind, "
                    " entity_type, entity_id, value, sketch, tags) "
                    "VALUES (:window, :org, :run, :name, :kind, "
                    "        :entity_type, :entity, :value, :sketch, CAST(:tags AS jsonb))"
                ),
                parameters,
            )
        else:
            await session.execute(
                sa.text(
                    "UPDATE performance_metrics "
                    "SET sketch = :sketch, tags = CAST(:tags AS jsonb) "
                    "WHERE run_id = :run AND entity_id = :entity AND time = :window"
                ),
                parameters,
            )
