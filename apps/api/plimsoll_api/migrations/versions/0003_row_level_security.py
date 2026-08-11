"""Row-level security.

PostgreSQL does not apply policies to a table's owner and never to a
superuser, so the runtime role must own nothing and every table is FORCEd.

Revision ID: 0003_row_level_security
Revises: 0002_initial_schema
"""

from __future__ import annotations

from alembic import op

revision = "0003_row_level_security"
down_revision = "0002_initial_schema"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "users",
    "projects",
    "project_run_counters",
    "credentials",
    "script_repos",
    "script_versions",
    "performance_tests",
    "performance_test_plans",
    "sla_rules",
    "generator_pools",
    "test_runs",
    "run_generators",
    "run_errors",
    "baselines",
    "idempotency_keys",
    "api_keys",
    "webhook_subscriptions",
    "audit_logs",
    "target_policies",
    "refresh_token_families",
    "performance_metrics",
]

ORG_PREDICATE = "organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid"


def upgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({ORG_PREDICATE}) WITH CHECK ({ORG_PREDICATE})"
        )

    # organizations is keyed on its own primary key.
    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON organizations "
        "USING (id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)"
    )

    # project_members reaches its organisation through the project, which is
    # itself policy-protected, so the subquery cannot see a foreign project.
    op.execute("ALTER TABLE project_members ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE project_members FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON project_members USING ("
        "  EXISTS (SELECT 1 FROM projects p WHERE p.id = project_members.project_id))"
    )

    # Login must find a user before an organisation is known. This is the one
    # deliberate exception, and it returns nothing but the login columns.
    #
    # It runs as plimsoll_auth rather than plimsoll_owner: FORCE ROW LEVEL
    # SECURITY subjects even the table owner to tenant_isolation, so a
    # definer-owned lookup would return no rows at exactly the moment no
    # organisation is set. plimsoll_auth cannot log in and owns nothing else,
    # so the bypass is scoped to this one function body.
    op.execute(
        """
        CREATE FUNCTION auth_lookup_user(p_email text)
        RETURNS TABLE (id uuid, organization_id uuid, password_hash text,
                       status text, org_role text)
        LANGUAGE sql SECURITY DEFINER SET search_path = public STABLE AS $$
            SELECT id, organization_id, password_hash, status, org_role
            FROM users WHERE email = lower(p_email);
        $$
        """
    )
    op.execute("GRANT SELECT ON users TO plimsoll_auth")
    # Reassigning ownership requires the incoming owner to hold CREATE on the
    # schema. It is granted only for the transfer and revoked immediately, so
    # plimsoll_auth keeps no standing power to create objects.
    op.execute("GRANT CREATE ON SCHEMA public TO plimsoll_auth")
    op.execute("ALTER FUNCTION auth_lookup_user(text) OWNER TO plimsoll_auth")
    op.execute("REVOKE CREATE ON SCHEMA public FROM plimsoll_auth")
    op.execute("REVOKE EXECUTE ON FUNCTION auth_lookup_user(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION auth_lookup_user(text) TO plimsoll_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth_lookup_user(text)")
    op.execute("REVOKE SELECT ON users FROM plimsoll_auth")
    for table in [*TENANT_TABLES, "organizations", "project_members"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
