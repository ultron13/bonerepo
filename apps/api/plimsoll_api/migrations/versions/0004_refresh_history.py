"""Refresh token history.

Family revocation on *reuse* requires knowing that a presented token was once
valid, which the single current_hash column cannot answer.

Revision ID: 0004_refresh_history
Revises: 0003_row_level_security
"""

from __future__ import annotations

from alembic import op

revision = "0004_refresh_history"
down_revision = "0003_row_level_security"
branch_labels = None
depends_on = None

ORG_PREDICATE = "organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE refresh_token_history (
            token_hash       VARCHAR(128) PRIMARY KEY,
            family_id        UUID NOT NULL REFERENCES refresh_token_families(id) ON DELETE CASCADE,
            organization_id  UUID NOT NULL REFERENCES organizations(id),
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("ALTER TABLE refresh_token_history ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE refresh_token_history FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON refresh_token_history "
        f"USING ({ORG_PREDICATE}) WITH CHECK ({ORG_PREDICATE})"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS refresh_token_history")
