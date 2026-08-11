"""Pre-authentication lookup for a presented refresh token.

A refresh token is opaque, so the organisation is unknown until the token is
resolved -- the same chicken-and-egg problem login has, and solved the same
way. The function returns nothing but an organisation identifier, to a caller
that already holds the secret it was derived from. Everything else about
rotation then happens inside the ordinary policies.

Revision ID: 0005_refresh_lookup
Revises: 0004_refresh_history
"""

from __future__ import annotations

from alembic import op

revision = "0005_refresh_lookup"
down_revision = "0004_refresh_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION auth_org_for_refresh(p_token_hash text)
        RETURNS uuid
        LANGUAGE sql SECURITY DEFINER SET search_path = public STABLE AS $$
            SELECT organization_id FROM (
                SELECT organization_id FROM refresh_token_families
                WHERE current_hash = p_token_hash
                UNION ALL
                SELECT organization_id FROM refresh_token_history
                WHERE token_hash = p_token_hash
            ) AS candidates
            LIMIT 1;
        $$
        """
    )
    op.execute("GRANT SELECT ON refresh_token_families TO plimsoll_auth")
    op.execute("GRANT SELECT ON refresh_token_history TO plimsoll_auth")
    op.execute("GRANT CREATE ON SCHEMA public TO plimsoll_auth")
    op.execute("ALTER FUNCTION auth_org_for_refresh(text) OWNER TO plimsoll_auth")
    op.execute("REVOKE CREATE ON SCHEMA public FROM plimsoll_auth")
    op.execute("REVOKE EXECUTE ON FUNCTION auth_org_for_refresh(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION auth_org_for_refresh(text) TO plimsoll_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth_org_for_refresh(text)")
    op.execute("REVOKE SELECT ON refresh_token_families FROM plimsoll_auth")
    op.execute("REVOKE SELECT ON refresh_token_history FROM plimsoll_auth")
