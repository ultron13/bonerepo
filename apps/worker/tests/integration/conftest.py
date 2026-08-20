"""The worker's integration tests talk to the stack `make dev` brings up.

The worker package has no conftest of its own above this one, so the settings
the API's tests inherit from theirs are established here instead. The addresses
are the ones compose publishes on the host, not the ones inside its network.
"""

import os

os.environ.setdefault(
    "PLIMSOLL_DATABASE_URL",
    "postgresql+asyncpg://plimsoll_app:plimsoll_app_dev@localhost:5432/plimsoll",
)
os.environ.setdefault(
    "PLIMSOLL_MIGRATION_DATABASE_URL",
    "postgresql+asyncpg://plimsoll_owner:plimsoll_owner_dev@localhost:5432/plimsoll",
)
os.environ.setdefault("PLIMSOLL_REDIS_URL", "redis://localhost:6379/0")
# SigV4 signs the Host header, so a presigned URL is signed for whoever fetches
# it. These tests fetch from the host.
os.environ.setdefault("PLIMSOLL_S3_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("PLIMSOLL_JWT_SECRET", "development-only-secret-change-me")
os.environ.setdefault(
    "PLIMSOLL_CREDENTIAL_KEY",
    "ZGV2ZWxvcG1lbnQtb25seS1rZXktMzItYnl0ZXMhISE=",
)

from collections.abc import AsyncIterator

import pytest

from plimsoll_api.db import session as db_session


@pytest.fixture(autouse=True)
async def _dispose_pooled_connections() -> AsyncIterator[None]:
    """The engine is cached across tests but each test gets its own event loop.

    Pooled asyncpg connections are bound to the loop that opened them, so a
    connection reused by the next test raises "Event loop is closed". Dropping
    the pool after each test costs a reconnect and removes the coupling.
    """
    yield
    await db_session.get_engine().dispose()
