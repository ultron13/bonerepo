"""Answering whether a slug is taken, before there is an organisation to ask as.

Creating a tenant runs inside a session scoped to the tenant being created,
because every tenant table is FORCEd -- including `organizations`, whose policy
matches on its own id. That means a direct read for an existing slug is
filtered to nothing and always answers "free", so the unique constraint would
be what refused a duplicate, as an integrity error rather than a conflict.

Same solution as every other pre-authentication lookup here: a `SECURITY
DEFINER` function that answers one question and returns nothing else. It says
whether a slug exists, not whose it is.

Revision ID: 0013_slug_lookup
Revises: 0012_session_purge
"""

from __future__ import annotations

from alembic import op

revision = "0013_slug_lookup"
down_revision = "0012_session_purge"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION auth_slug_taken(p_slug text)
        RETURNS TABLE (taken boolean)
        LANGUAGE sql SECURITY DEFINER SET search_path = public STABLE AS $$
            SELECT true FROM organizations WHERE slug = lower(p_slug) LIMIT 1;
        $$
        """
    )
    op.execute("GRANT CREATE ON SCHEMA public TO plimsoll_auth")
    op.execute("ALTER FUNCTION auth_slug_taken(text) OWNER TO plimsoll_auth")
    op.execute("REVOKE CREATE ON SCHEMA public FROM plimsoll_auth")
    op.execute("REVOKE EXECUTE ON FUNCTION auth_slug_taken(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION auth_slug_taken(text) TO plimsoll_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth_slug_taken(text)")
