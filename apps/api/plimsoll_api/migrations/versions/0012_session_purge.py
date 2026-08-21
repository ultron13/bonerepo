"""Clearing finished sign-in sessions, across every organisation.

A refresh family is written on every sign-in and lives fourteen days; its
history grows once per rotation, and cascades away with it. Nothing read
either after the family was dead and nothing ever deleted them, so both grew
with use and shrank never.

The work has to cross organisations, and row-level security is forced on both
tables -- so it goes through a `SECURITY DEFINER` function, the same way the
pre-authentication lookups do. Narrow on purpose: it deletes families that are
already revoked or already expired and nothing else, takes no arguments that
could widen it, and returns a count. A maintenance job with a superuser
connection would have been the alternative, and would have meant a credential
that can do anything sitting in the worker for the sake of one DELETE.

Revision ID: 0012_session_purge
Revises: 0011_metrics_retention
"""

from __future__ import annotations

from alembic import op

revision = "0012_session_purge"
down_revision = "0011_metrics_retention"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION maintenance_purge_dead_sessions()
        RETURNS bigint
        LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
        DECLARE
            removed bigint;
        BEGIN
            WITH dead AS (
                DELETE FROM refresh_token_families
                -- A revoked family is kept a week: revocation is the theft
                -- signal, and somebody looking into one wants it still there.
                WHERE (revoked_at IS NOT NULL AND revoked_at < now() - INTERVAL '7 days')
                -- An expired one cannot be rotated, so it is already useless;
                -- the day's margin keeps the boundary clear of clock skew.
                   OR (expires_at < now() - INTERVAL '1 day')
                RETURNING id
            )
            SELECT count(*) INTO removed FROM dead;
            RETURN removed;
        END;
        $$
        """
    )
    op.execute("GRANT DELETE, SELECT ON refresh_token_families TO plimsoll_auth")
    op.execute("GRANT DELETE, SELECT ON refresh_token_history TO plimsoll_auth")
    op.execute("GRANT CREATE ON SCHEMA public TO plimsoll_auth")
    op.execute("ALTER FUNCTION maintenance_purge_dead_sessions() OWNER TO plimsoll_auth")
    op.execute("REVOKE CREATE ON SCHEMA public FROM plimsoll_auth")
    op.execute("REVOKE EXECUTE ON FUNCTION maintenance_purge_dead_sessions() FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION maintenance_purge_dead_sessions() TO plimsoll_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS maintenance_purge_dead_sessions()")
    op.execute("REVOKE DELETE, SELECT ON refresh_token_history FROM plimsoll_auth")
    op.execute("REVOKE DELETE, SELECT ON refresh_token_families FROM plimsoll_auth")
