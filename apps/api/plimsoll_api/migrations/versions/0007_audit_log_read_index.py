"""An index for reading the audit trail.

The table carried only its primary key, which is fine for a log that is written
and never read. Reading it is the point -- an account of what happened, newest
first, filtered by action or by the thing acted on -- and every one of those
queries was a sequential scan over a table that grows for the life of the
deployment.

The ordering columns match the cursor: `(created_at DESC, id DESC)` is what the
keyset pagination sorts and compares on, so the index serves the scan and the
seek both.

Revision ID: 0007_audit_log_read_index
Revises: 0006_metrics_unique_window
"""

from __future__ import annotations

from alembic import op

revision = "0007_audit_log_read_index"
down_revision = "0006_metrics_unique_window"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS audit_logs_org_recent
        ON audit_logs (organization_id, created_at DESC, id DESC)
        """
    )
    # Filtering by what was acted on is how an investigation actually starts:
    # someone asks what happened to this run, not what happened at this time.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS audit_logs_entity
        ON audit_logs (organization_id, entity_id, created_at DESC)
        WHERE entity_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS audit_logs_entity")
    op.execute("DROP INDEX IF EXISTS audit_logs_org_recent")
