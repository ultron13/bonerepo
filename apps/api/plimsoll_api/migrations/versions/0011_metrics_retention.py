"""Metrics stop growing for ever.

`performance_metrics` is a hypertable and nothing was ever removing from it.
An hour-long test at five hundred virtual users writes a row per transaction
per generator per window, so the table grows with every run and never shrinks.
The first symptom is a disk filling on the night somebody runs a big test,
which is exactly the night nobody wants to be resizing a volume.

Ninety days is a default rather than a rule; an operator who has to keep more
changes it. What ages out is the per-window detail. The runs, their SLA
verdicts and their merged summaries live in ordinary tables and are kept
regardless, because those are the results -- what goes is the working that
produced them.

**Compression is deliberately not enabled**, and not by preference:
TimescaleDB refuses it on a table with row-level security, and row-level
security is the tenant boundary (invariant 4). Compression would store these
rows roughly an order of magnitude smaller, so this is a real cost, paid for a
guarantee that is worth more than the disk. Anybody revisiting it should know
the trade rather than rediscover the error.

Revision ID: 0011_metrics_retention
Revises: 0010_identity_providers
"""

from __future__ import annotations

import os

from alembic import op

revision = "0011_metrics_retention"
down_revision = "0010_identity_providers"
branch_labels = None
depends_on = None

RETAIN_FOR = os.environ.get("PLIMSOLL_METRICS_RETENTION", "90 days")


def upgrade() -> None:
    op.execute(
        f"SELECT add_retention_policy('performance_metrics', INTERVAL '{RETAIN_FOR}', "
        f"if_not_exists => true)"
    )


def downgrade() -> None:
    op.execute("SELECT remove_retention_policy('performance_metrics', if_exists => true)")
