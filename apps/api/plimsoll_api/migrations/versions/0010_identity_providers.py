"""Single sign-on, configured per organisation.

The client secret is stored the way every other secret here is: encrypted with
the credential key, with the key reference beside it, so a rotation moves it
along with the rest.

The lookup that starts a sign-in has the same chicken-and-egg problem login
has -- the organisation is what is being established, so the read that finds it
cannot already be scoped to one. Same solution: a `SECURITY DEFINER` function
returning only what starting a flow needs, and never the client secret. The
secret is used at the token exchange, which happens after the organisation is
known and inside a scoped session.

Revision ID: 0010_identity_providers
Revises: 0009_run_listing_indexes
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_identity_providers"
down_revision = "0009_run_listing_indexes"
branch_labels = None
depends_on = None

ORG_PREDICATE = "organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid"


def upgrade() -> None:
    op.create_table(
        "identity_providers",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("issuer", sa.Text(), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("client_secret_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("client_secret_key_ref", sa.Text(), nullable=False),
        # Which claim carries group membership, and which group grants
        # administration. Naming the claim rather than assuming "groups" is
        # what makes this work with providers that call it something else.
        sa.Column("groups_claim", sa.Text(), nullable=False, server_default="groups"),
        sa.Column("admin_group", sa.Text(), nullable=True),
        # Email domains this provider may create accounts for. Empty means
        # none: a provider that has not said which domains it speaks for
        # should not be able to create an account for any address at all.
        sa.Column(
            "allowed_domains",
            sa.dialects.postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default="{}",
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_by", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        # One per organisation in v0.3. Two would need a way for a person to
        # say which one they are using, and there is no good place to ask.
        sa.UniqueConstraint("organization_id", name="identity_providers_organization_id_key"),
    )
    op.execute("ALTER TABLE identity_providers ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE identity_providers FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON identity_providers "
        f"USING ({ORG_PREDICATE}) WITH CHECK ({ORG_PREDICATE})"
    )

    # Which provider a person is being sent to, resolved from the organisation
    # slug they typed. Returns nothing secret: the client id is public by
    # design in an authorisation-code flow, and the secret is deliberately not
    # in the result.
    op.execute(
        """
        CREATE FUNCTION auth_lookup_identity_provider(p_slug text)
        RETURNS TABLE (
            id uuid, organization_id uuid, issuer text, client_id text
        )
        LANGUAGE sql SECURITY DEFINER SET search_path = public STABLE AS $$
            SELECT p.id, p.organization_id, p.issuer, p.client_id
            FROM identity_providers p
            JOIN organizations o ON o.id = p.organization_id
            WHERE o.slug = lower(p_slug)
              AND p.enabled
              AND o.status = 'ACTIVE'
            LIMIT 1;
        $$
        """
    )
    op.execute("GRANT SELECT ON identity_providers TO plimsoll_auth")
    op.execute("GRANT SELECT ON organizations TO plimsoll_auth")
    op.execute("GRANT CREATE ON SCHEMA public TO plimsoll_auth")
    op.execute("ALTER FUNCTION auth_lookup_identity_provider(text) OWNER TO plimsoll_auth")
    op.execute("REVOKE CREATE ON SCHEMA public FROM plimsoll_auth")
    op.execute("REVOKE EXECUTE ON FUNCTION auth_lookup_identity_provider(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION auth_lookup_identity_provider(text) TO plimsoll_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth_lookup_identity_provider(text)")
    op.execute("REVOKE SELECT ON identity_providers FROM plimsoll_auth")
    op.drop_table("identity_providers")
