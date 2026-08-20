"""One row per window, whoever reported it.

S4a merged sketches within the batch they arrived in and appended the result.
Two generators whose windows land in different reads therefore left two rows
for one (run, transaction, window), and a redelivered message left a third. The
run summary stayed exact because it merges every row it finds, but a per-window
read -- which live streaming and the continuous aggregates both do -- sees the
window more than once.

A unique index is permitted on a hypertable as long as it carries the
partitioning column, which `time` is. With it, the writer can merge on conflict
instead of appending, and at-least-once delivery stops being a counting error.

Revision ID: 0006_metrics_unique_window
Revises: 0005_refresh_lookup
"""

from __future__ import annotations

from alembic import op

revision = "0006_metrics_unique_window"
down_revision = "0005_refresh_lookup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Existing rows may already duplicate a key, and the index cannot be built
    # over them. One row per key survives and the rest are dropped. This is
    # development data only -- no released version has written this table --
    # so losing a duplicate window's samples is acceptable where refusing to
    # migrate would not be.
    #
    # ctid alone is not enough to identify a row here: it is unique within a
    # chunk, and a hypertable has many, so two rows in different chunks can
    # share one. tableoid names the chunk and makes the pair unique.
    op.execute(
        """
        WITH ranked AS (
            SELECT ctid, tableoid,
                   row_number() OVER (
                       PARTITION BY run_id, entity_id, time ORDER BY ctid
                   ) AS rn
            FROM performance_metrics
        )
        DELETE FROM performance_metrics p
        USING ranked r
        WHERE p.ctid = r.ctid AND p.tableoid = r.tableoid AND r.rn > 1
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX metrics_one_row_per_window
        ON performance_metrics (run_id, entity_id, time)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS metrics_one_row_per_window")
