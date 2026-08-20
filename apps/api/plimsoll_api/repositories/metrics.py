"""Reading merged windows back.

Deliberately dumb: this returns sketches and scalars, and computes nothing.
A percentile in this file would be a percentile computed per window and then
combined, which is the exact mistake ADR-0004 forbids.
"""

from __future__ import annotations

import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def windows_for_run(session: AsyncSession, run_id: uuid.UUID) -> list[sa.Row[Any]]:
    return list(
        (
            await session.execute(
                sa.text(
                    "SELECT time, entity_id, sketch, tags "
                    "FROM performance_metrics "
                    "WHERE run_id = :run AND metric_kind = 'histogram' "
                    "  AND entity_type = 'transaction' "
                    "ORDER BY entity_id, time"
                ),
                {"run": run_id},
            )
        ).all()
    )
