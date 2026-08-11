"""Extensions and baseline grants.

Revision ID: 0001_extensions
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "0001_extensions"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    # Anything plimsoll_owner creates from here on is usable by the runtime role.
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE plimsoll_owner IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO plimsoll_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE plimsoll_owner IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO plimsoll_app"
    )


def downgrade() -> None:
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE plimsoll_owner IN SCHEMA public "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM plimsoll_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE plimsoll_owner IN SCHEMA public "
        "REVOKE USAGE, SELECT ON SEQUENCES FROM plimsoll_app"
    )
    op.execute("DROP EXTENSION IF EXISTS timescaledb")
