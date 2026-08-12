"""Idempotent development seed.

Runs as plimsoll_owner, but that grants no exemption: every tenant table is
FORCEd, so the seed must scope itself to an organisation exactly as a request
does. It cannot look the demo organisation up first -- a policy keyed on
app.current_org_id hides the row until the setting is already correct -- so the
identifier is derived deterministically from the slug instead. That also makes
re-running the seed a no-op rather than a second organisation.
"""

from __future__ import annotations

import asyncio
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from plimsoll_api.config import get_settings
from plimsoll_api.security.passwords import hash_password
from plimsoll_api.security.secrets import get_key_provider

DEMO_SLUG = "demo"
DEMO_ORG_ID = uuid.uuid5(uuid.NAMESPACE_DNS, "demo.plimsoll.dev")
DEMO_PASSWORD = "plimsoll-demo-password"  # noqa: S105 - development seed only
DEMO_USERS = [
    ("admin@demo.plimsoll.dev", "Demo Admin", "ORG_ADMIN"),
    ("viewer@demo.plimsoll.dev", "Demo Viewer", "VIEWER"),
]
# The allowlist is empty by default and permits no runs (ADR-0007). The demo
# works because it ships its own permitted target, not because the check is
# soft.
DEMO_ALLOWLIST = '["demo-target"]'
# Two generators of 500 users each: enough for the demo test to pass preflight,
# small enough to run on a laptop. S3 gives the runtime meaning.
DEMO_POOL_CONFIG = '{"image": "ghcr.io/ultron13/generator:jmeter-5.6.3"}'
# The fixture's Basic-auth password. A development fixture, not a secret.
FIXTURE_TOKEN = "plimsoll:plimsoll-fixture-token"  # noqa: S105 - development seed only
FIXTURE_REPO_URL = "http://script-fixture/private/plans.git"
# The demo plan reaches ${API_HOST}; preflight resolves it from this variable
# and checks the result against the allowlist.
DEMO_API_HOST = "demo-target"


async def seed() -> None:
    engine = create_async_engine(get_settings().migration_database_url)
    async with engine.begin() as connection:
        await connection.execute(
            sa.text("SELECT set_config('app.current_org_id', :org, true)"),
            {"org": str(DEMO_ORG_ID)},
        )

        await connection.execute(
            sa.text(
                "INSERT INTO organizations (id, name, slug) "
                "VALUES (:id, 'Demo organisation', :slug) ON CONFLICT (id) DO NOTHING"
            ),
            {"id": DEMO_ORG_ID, "slug": DEMO_SLUG},
        )

        for email, name, role in DEMO_USERS:
            await connection.execute(
                sa.text(
                    "INSERT INTO users "
                    "(id, organization_id, email, name, password_hash, org_role) "
                    "VALUES (:id, :org, :email, :name, :hash, :role) "
                    "ON CONFLICT (organization_id, email) DO NOTHING"
                ),
                {
                    "id": uuid.uuid4(),
                    "org": DEMO_ORG_ID,
                    "email": email,
                    "name": name,
                    "hash": hash_password(DEMO_PASSWORD),
                    "role": role,
                },
            )

        await connection.execute(
            sa.text(
                "INSERT INTO projects (id, organization_id, name, project_key, environment) "
                "VALUES (:id, :org, 'Demo project', 'DEMO', 'development') "
                "ON CONFLICT (organization_id, project_key) DO NOTHING"
            ),
            {"id": uuid.uuid4(), "org": DEMO_ORG_ID},
        )

        await connection.execute(
            sa.text(
                "INSERT INTO generator_pools "
                "(id, organization_id, name, runtime, config, max_generators, "
                " max_vus_per_generator) "
                "VALUES (:id, :org, 'local-docker', 'docker', CAST(:config AS jsonb), 2, 500) "
                "ON CONFLICT (organization_id, name) DO NOTHING"
            ),
            {"id": uuid.uuid4(), "org": DEMO_ORG_ID, "config": DEMO_POOL_CONFIG},
        )

        for name, secret in (
            ("fixture-git-token", FIXTURE_TOKEN),
            ("API_HOST", DEMO_API_HOST),
        ):
            ciphertext, key_ref = get_key_provider().encrypt(secret.encode())
            await connection.execute(
                sa.text(
                    "INSERT INTO credentials "
                    "(id, organization_id, name, kind, ciphertext, key_ref) "
                    "VALUES (:id, :org, :name, :kind, :ciphertext, :key_ref) "
                    "ON CONFLICT (organization_id, name) DO NOTHING"
                ),
                {
                    "id": uuid.uuid4(),
                    "org": DEMO_ORG_ID,
                    "name": name,
                    "kind": "GIT_TOKEN" if name == "fixture-git-token" else "VARIABLE",
                    "ciphertext": ciphertext,
                    "key_ref": key_ref,
                },
            )

        await connection.execute(
            sa.text(
                "INSERT INTO script_repos "
                "(id, organization_id, project_id, name, repo_url, default_ref, plan_path, "
                " credential_id) "
                "SELECT :id, :org, p.id, 'Demo checkout plan', :url, 'main', "
                "       'perf/checkout.jmx', c.id "
                "FROM projects p, credentials c "
                "WHERE p.project_key = 'DEMO' AND c.name = 'fixture-git-token' "
                "  AND NOT EXISTS "
                "      (SELECT 1 FROM script_repos WHERE name = 'Demo checkout plan')"
            ),
            {"id": uuid.uuid4(), "org": DEMO_ORG_ID, "url": FIXTURE_REPO_URL},
        )

        await connection.execute(
            sa.text(
                "INSERT INTO target_policies (id, organization_id, version, allowlist) "
                "VALUES (:id, :org, 1, CAST(:allowlist AS jsonb)) "
                "ON CONFLICT (organization_id, version) DO NOTHING"
            ),
            {"id": uuid.uuid4(), "org": DEMO_ORG_ID, "allowlist": DEMO_ALLOWLIST},
        )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
