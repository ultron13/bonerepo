"""Integration tests talk to the stack that `make dev` brings up.

The parent conftest sets placeholder connection strings so unit tests can build
the application without any infrastructure. These override them with the
addresses the compose stack publishes on the host.
"""

import os
import uuid
from collections.abc import AsyncIterator, Iterator

import httpx
import pytest

os.environ["PLIMSOLL_DATABASE_URL"] = os.environ.get(
    "PLIMSOLL_TEST_APP_ASYNC_URL",
    "postgresql+asyncpg://plimsoll_app:plimsoll_app_dev@localhost:5432/plimsoll",
)
os.environ["PLIMSOLL_MIGRATION_DATABASE_URL"] = os.environ.get(
    "PLIMSOLL_TEST_MIGRATION_ASYNC_URL",
    "postgresql+asyncpg://plimsoll_owner:plimsoll_owner_dev@localhost:5432/plimsoll",
)
# Matches the compose stack's key: a test calling a service in-process (rather
# than through the running API) decrypts with this key, and a mismatch here
# reads as ciphertext corruption rather than a config problem.
os.environ["PLIMSOLL_CREDENTIAL_KEY"] = os.environ.get(
    "PLIMSOLL_TEST_CREDENTIAL_KEY",
    "ZGV2ZWxvcG1lbnQtb25seS1rZXktMzItYnl0ZXMhISE=",
)
# Also the compose stack's. Tests normally receive their tokens from the API's
# own login endpoint, but an agent token is minted here and presented to the
# running API, so the two processes must sign with the same secret.
os.environ["PLIMSOLL_JWT_SECRET"] = os.environ.get(
    "PLIMSOLL_TEST_JWT_SECRET",
    "development-only-secret-change-me",
)
# SigV4 signs the Host header, so a presigned URL cannot be rewritten after it
# is signed -- it has to be signed for the host that will fetch it. These tests
# run on the host, so they sign against the port compose publishes.
os.environ["PLIMSOLL_S3_ENDPOINT"] = os.environ.get(
    "PLIMSOLL_TEST_S3_URL",
    "http://localhost:9000",
)

from plimsoll_api import storage
from plimsoll_api.config import get_settings
from plimsoll_api.db import session as db_session
from plimsoll_api.security import secrets

# The settings and engine are cached. Drop anything built from the placeholder
# values so a mixed unit-and-integration run still reaches the real database.
get_settings.cache_clear()
db_session.get_engine.cache_clear()
db_session._session_factory.cache_clear()
secrets.get_key_provider.cache_clear()
storage._client.cache_clear()
storage._public_client.cache_clear()


@pytest.fixture(autouse=True)
async def _dispose_pooled_connections() -> AsyncIterator[None]:
    """The engine is cached across tests but each test gets its own event loop.

    Pooled asyncpg connections are bound to the loop that opened them, so a
    connection reused by the next test raises "Event loop is closed". Dropping
    the pool after each test costs a reconnect and removes the coupling.
    """
    yield
    await db_session.get_engine().dispose()


API_URL = os.environ.get("PLIMSOLL_TEST_API_URL", "http://localhost:8000")
ADMIN = {"email": "admin@demo.plimsoll.dev", "password": "plimsoll-demo-password"}
VIEWER = {"email": "viewer@demo.plimsoll.dev", "password": "plimsoll-demo-password"}


def _signed_in(account: dict[str, str]) -> Iterator[httpx.Client]:
    """Signing in happens inside the context manager.

    A client is opened implicitly by its first request, and opening it again by
    entering `with` afterwards raises.
    """
    with httpx.Client(base_url=API_URL, timeout=30) as client:
        token = client.post("/api/v1/auth/login", json=account).json()["accessToken"]
        client.headers["Authorization"] = f"Bearer {token}"
        yield client


@pytest.fixture
def admin_client() -> Iterator[httpx.Client]:
    yield from _signed_in(ADMIN)


@pytest.fixture
def viewer_client() -> Iterator[httpx.Client]:
    yield from _signed_in(VIEWER)


@pytest.fixture
def admin_org(admin_client: httpx.Client) -> uuid.UUID:
    """The signed-in organisation, resolved before the test body runs.

    An async test that called the synchronous client itself would block the
    loop it is running on.
    """
    return uuid.UUID(admin_client.get("/api/v1/auth/me").json()["organizationId"])


@pytest.fixture(scope="session")
def completed_run() -> str:
    """One real run, executed once and reused: it takes about a minute."""
    from tests.integration.test_run_execution import TERMINAL, _await_status, _short_test

    with httpx.Client(base_url=API_URL, timeout=30) as client:
        token = client.post("/api/v1/auth/login", json=ADMIN).json()["accessToken"]
        client.headers["Authorization"] = f"Bearer {token}"
        test_id = _short_test(client, seconds=15, users=2)
        run_id = client.post(f"/api/v1/tests/{test_id}/runs").json()["id"]
        _await_status(client, run_id, TERMINAL, timeout=300)
        return str(run_id)
