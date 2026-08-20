"""Indexes that match how runs are actually listed.

Two listings order by `(created_at DESC, id DESC)` -- the organisation's
recent runs, and one project's -- and neither had an index that could satisfy
it. Postgres was reading every run in scope and sorting. That is invisible on
a demo and is the first thing to hurt on an instance with history, because the
landing page is the query that runs most often.

The trailing columns match the keyset cursor exactly, so the plan is an index
scan with no sort step at all rather than an index scan followed by one.

Revision ID: 0009_run_listing_indexes
Revises: 0008_api_key_lookup
"""

from __future__ import annotations

from alembic import op

revision = "0009_run_listing_indexes"
down_revision = "0008_api_key_lookup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS test_runs_recent_idx "
        "ON test_runs (organization_id, created_at DESC, id DESC)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS test_runs_project_recent_idx "
        "ON test_runs (project_id, created_at DESC, id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS test_runs_project_recent_idx")
    op.execute("DROP INDEX IF EXISTS test_runs_recent_idx")
