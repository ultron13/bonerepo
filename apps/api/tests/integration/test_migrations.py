import os

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration

OWNER_URL = os.environ.get(
    "PLIMSOLL_TEST_MIGRATION_URL",
    "postgresql+psycopg://plimsoll_owner:plimsoll_owner_dev@localhost:5432/plimsoll",
)


def test_timescaledb_extension_is_installed() -> None:
    engine = sa.create_engine(OWNER_URL)
    with engine.connect() as connection:
        result = connection.execute(
            sa.text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
        ).scalar()
    assert result == 1


def test_alembic_is_at_head() -> None:
    engine = sa.create_engine(OWNER_URL)
    with engine.connect() as connection:
        version = connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar()
    assert version is not None
