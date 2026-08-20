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

from plimsoll_contracts.metrics import MetricKind, decode_sketch, new_sketch, to_bytes

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
    """Upsert, because at-least-once delivery means a window may arrive twice.

    A repeat carries the same merged sketch for the same key, so replacing the
    row is idempotent where adding to it would double-count.
    """
    for row in rows:
        await session.execute(
            sa.text(
                "INSERT INTO performance_metrics "
                "(time, organization_id, run_id, metric_name, metric_kind, "
                " entity_type, entity_id, value, sketch, tags) "
                "VALUES (:window, :org, :run, :name, :kind, "
                "        :entity_type, :entity, :value, :sketch, CAST(:tags AS jsonb))"
            ),
            {
                # A datetime rather than its ISO text: asyncpg infers the
                # parameter type from the column and refuses a string for it.
                "window": datetime.fromisoformat(row.window_start),
                "org": row.organization_id,
                "run": row.run_id,
                "name": METRIC_NAME,
                "kind": str(MetricKind.HISTOGRAM),
                "entity_type": ENTITY_TYPE,
                "entity": row.transaction,
                # A histogram row carries a sketch, never a value: exactly one
                # of the two is populated, decided by the kind.
                "value": None,
                "sketch": to_bytes(row.sketch),
                # The scalars beside the sketch: enough to answer counts and
                # throughput without decoding a histogram.
                "tags": json.dumps(
                    {
                        "count": row.count,
                        "errorCount": row.error_count,
                        "min": row.minimum,
                        "max": row.maximum,
                        "total": row.total,
                    }
                ),
            },
        )
