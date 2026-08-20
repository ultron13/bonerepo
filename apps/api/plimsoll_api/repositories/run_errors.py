"""Grouped failures, summed as they arrive."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def upsert(
    session: AsyncSession, org_id: uuid.UUID, run_id: uuid.UUID, group: dict[str, str]
) -> None:
    """One row per (run, fingerprint), counts summed and the window widened.

    Two generators hitting the same fault are one problem seen twice, so the
    counts add and first/last seen stretch to cover both.
    """
    await session.execute(
        sa.text(
            "INSERT INTO run_errors "
            "(id, organization_id, run_id, fingerprint, error_code, message, transaction, "
            " count, first_seen, last_seen, sample_detail) "
            "VALUES (:id, :org, :run, :fingerprint, :code, :message, :transaction, "
            "        :count, :first_seen, :last_seen, CAST(:sample AS jsonb)) "
            "ON CONFLICT (run_id, fingerprint) DO UPDATE SET "
            "  count = run_errors.count + EXCLUDED.count, "
            "  first_seen = LEAST(run_errors.first_seen, EXCLUDED.first_seen), "
            "  last_seen = GREATEST(run_errors.last_seen, EXCLUDED.last_seen)"
        ),
        {
            "id": uuid.uuid4(),
            "org": org_id,
            "run": run_id,
            "fingerprint": group["fingerprint"],
            "code": group.get("errorCode", "")[:100],
            "message": group.get("message", ""),
            "transaction": group.get("transaction", "")[:255],
            "count": int(group.get("count", 0)),
            "first_seen": datetime.fromtimestamp(float(group.get("firstSeen", 0)), UTC),
            "last_seen": datetime.fromtimestamp(float(group.get("lastSeen", 0)), UTC),
            "sample": json.dumps({"detail": group.get("sample", "")}),
        },
    )


async def for_run(session: AsyncSession, run_id: uuid.UUID) -> list[sa.Row[Any]]:
    return list(
        (
            await session.execute(
                sa.text(
                    "SELECT fingerprint, error_code, message, transaction, count, "
                    "       first_seen, last_seen, sample_detail "
                    "FROM run_errors WHERE run_id = :run ORDER BY count DESC, fingerprint"
                ),
                {"run": run_id},
            )
        ).all()
    )
