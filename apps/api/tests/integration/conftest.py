"""Integration tests talk to the stack that `make dev` brings up.

The parent conftest sets placeholder connection strings so unit tests can build
the application without any infrastructure. These override them with the
addresses the compose stack publishes on the host.
"""

import os
from collections.abc import AsyncIterator

import pytest

os.environ["PLIMSOLL_DATABASE_URL"] = os.environ.get(
    "PLIMSOLL_TEST_APP_ASYNC_URL",
    "postgresql+asyncpg://plimsoll_app:plimsoll_app_dev@localhost:5432/plimsoll",
)
os.environ["PLIMSOLL_MIGRATION_DATABASE_URL"] = os.environ.get(
    "PLIMSOLL_TEST_MIGRATION_ASYNC_URL",
    "postgresql+asyncpg://plimsoll_owner:plimsoll_owner_dev@localhost:5432/plimsoll",
)

from plimsoll_api.config import get_settings
from plimsoll_api.db import session as db_session

# The settings and engine are cached. Drop anything built from the placeholder
# values so a mixed unit-and-integration run still reaches the real database.
get_settings.cache_clear()
db_session.get_engine.cache_clear()
db_session._session_factory.cache_clear()


@pytest.fixture(autouse=True)
async def _dispose_pooled_connections() -> AsyncIterator[None]:
    """The engine is cached across tests but each test gets its own event loop.

    Pooled asyncpg connections are bound to the loop that opened them, so a
    connection reused by the next test raises "Event loop is closed". Dropping
    the pool after each test costs a reconnect and removes the coupling.
    """
    yield
    await db_session.get_engine().dispose()
