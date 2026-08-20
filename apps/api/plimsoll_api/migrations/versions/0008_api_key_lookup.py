"""Resolving an API key before its organisation is known.

The same chicken-and-egg problem login has, solved the same way: the key is
what establishes which organisation this is, so the lookup that finds it cannot
already be scoped to one, and row-level security refuses an unscoped read.

A `SECURITY DEFINER` function owned by a role that may read the table, granted
only to the runtime role, and returning only what authentication needs -- the
key's identifier, its organisation, and its scopes. It applies the revocation
and expiry conditions itself so a caller cannot forget them and authenticate a
revoked credential.

Revision ID: 0008_api_key_lookup
Revises: 0007_audit_log_read_index
"""

from __future__ import annotations

from alembic import op

revision = "0008_api_key_lookup"
down_revision = "0007_audit_log_read_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION auth_api_key(p_key_hash text)
        RETURNS TABLE (id uuid, organization_id uuid, scopes text[])
        LANGUAGE sql SECURITY DEFINER SET search_path = public STABLE AS $$
            SELECT id, organization_id, scopes
            FROM api_keys
            WHERE key_hash = p_key_hash
              AND revoked_at IS NULL
              AND (expires_at IS NULL OR expires_at > now())
            LIMIT 1;
        $$
        """
    )
    # Recording use is a write, and the same scoping problem applies: the row
    # is found before an organisation is established. Confined to one column
    # on one row, found by the hash the caller already proved it holds.
    op.execute(
        """
        CREATE FUNCTION auth_touch_api_key(p_key_hash text)
        RETURNS void
        LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
            UPDATE api_keys SET last_used_at = now() WHERE key_hash = p_key_hash;
        $$
        """
    )
    op.execute("GRANT SELECT, UPDATE ON api_keys TO plimsoll_auth")
    op.execute("GRANT CREATE ON SCHEMA public TO plimsoll_auth")
    op.execute("ALTER FUNCTION auth_api_key(text) OWNER TO plimsoll_auth")
    op.execute("ALTER FUNCTION auth_touch_api_key(text) OWNER TO plimsoll_auth")
    op.execute("REVOKE CREATE ON SCHEMA public FROM plimsoll_auth")
    op.execute("REVOKE EXECUTE ON FUNCTION auth_api_key(text) FROM PUBLIC")
    op.execute("REVOKE EXECUTE ON FUNCTION auth_touch_api_key(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION auth_api_key(text) TO plimsoll_app")
    op.execute("GRANT EXECUTE ON FUNCTION auth_touch_api_key(text) TO plimsoll_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth_touch_api_key(text)")
    op.execute("DROP FUNCTION IF EXISTS auth_api_key(text)")
    op.execute("REVOKE SELECT, UPDATE ON api_keys FROM plimsoll_auth")
