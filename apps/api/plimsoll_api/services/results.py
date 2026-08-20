"""Deriving percentiles, once, from a merged distribution.

Every window for a transaction is merged into one histogram and every
percentile comes off that one histogram. There is no code path here that reads
a percentile from a row, and there must not be: percentiles are order
statistics, and no arithmetic on summarised ones recovers the truth (ADR-0004,
invariant 2).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.repositories import metrics as repo
from plimsoll_api.services.runs import require
from plimsoll_contracts.metrics import WINDOW_SECONDS, from_bytes, new_sketch, percentile
from plimsoll_contracts.results import RunMetricsResponse, TransactionSummary


def _summarise(transaction: str, rows: list[Any]) -> TransactionSummary:
    merged = new_sketch()
    count = errors = total = 0
    minimum: int | None = None
    maximum = 0
    for row in rows:
        merged.add(from_bytes(row.sketch))
        tags = row.tags or {}
        count += int(tags.get("count", 0))
        errors += int(tags.get("errorCount", 0))
        total += int(tags.get("total", 0))
        maximum = max(maximum, int(tags.get("max", 0)))
        low = int(tags.get("min", 0))
        minimum = low if minimum is None else min(minimum, low)

    # The span the transaction was actually observed over, not the run's
    # length: a transaction that only appears in the last ten seconds has a
    # throughput measured over those ten seconds.
    span = (len(rows) or 1) * WINDOW_SECONDS
    return TransactionSummary(
        transaction=transaction,
        count=count,
        error_count=errors,
        error_rate=round(errors / count, 6) if count else 0.0,
        min=minimum or 0,
        max=maximum,
        mean=round(total / count, 2) if count else 0.0,
        p50=percentile(merged, 50),
        p90=percentile(merged, 90),
        p95=percentile(merged, 95),
        p99=percentile(merged, 99),
        throughput=round(count / span, 3),
    )


async def for_run(session: AsyncSession, run_id: uuid.UUID) -> RunMetricsResponse:
    # require() first: authorisation belongs to the run row, and a run that
    # does not exist has no results to describe.
    await require(session, run_id)
    rows = await repo.windows_for_run(session, run_id)

    grouped: dict[str, list[Any]] = {}
    for row in rows:
        grouped.setdefault(row.entity_id, []).append(row)

    transactions = [_summarise(name, owned) for name, owned in sorted(grouped.items())]
    return RunMetricsResponse(
        run_id=run_id,
        total_samples=sum(item.count for item in transactions),
        total_errors=sum(item.error_count for item in transactions),
        transactions=transactions,
    )
