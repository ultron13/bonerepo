import os

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration

OWNER_URL = os.environ.get(
    "PLIMSOLL_TEST_MIGRATION_URL",
    "postgresql+psycopg://plimsoll_owner:plimsoll_owner_dev@localhost:5432/plimsoll",
)

EXPECTED_TABLES = {
    "organizations",
    "users",
    "project_members",
    "projects",
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
    "performance_metrics",
    "target_policies",
    "refresh_token_families",
    "project_run_counters",
    "refresh_token_history",
}


def _inspector() -> sa.Inspector:
    return sa.inspect(sa.create_engine(OWNER_URL))


def test_every_table_exists() -> None:
    assert EXPECTED_TABLES <= set(_inspector().get_table_names())


def test_performance_metrics_is_a_hypertable() -> None:
    engine = sa.create_engine(OWNER_URL)
    with engine.connect() as connection:
        count = connection.execute(
            sa.text(
                "SELECT count(*) FROM timescaledb_information.hypertables "
                "WHERE hypertable_name = 'performance_metrics'"
            )
        ).scalar()
    assert count == 1


def test_every_tenant_table_carries_organization_id() -> None:
    inspector = _inspector()
    exempt = {"organizations", "project_members"}
    for table in EXPECTED_TABLES - exempt:
        columns = {c["name"] for c in inspector.get_columns(table)}
        assert "organization_id" in columns, f"{table} cannot be protected by RLS"
