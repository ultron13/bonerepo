"""A dependency being away answers 503, not 500.

The difference is not cosmetic. 500 tells a client the server has a bug: a CI
pipeline will not retry it, a load balancer will not take the instance out,
and the page goes to whoever owns the code rather than whoever owns the
database. 503 with Retry-After says come back, which is what is true.

Written after finding the API answered 500 for a stopped database, and after
the first fix caught only SQLAlchemy's OperationalError and never fired --
because a container that is stopped stops resolving, and the failure arrives
as socket.gaierror before any driver is involved.
"""

from __future__ import annotations

import socket

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import IntegrityError, OperationalError

from plimsoll_api.errors import register_error_handlers


def _app_that_raises(exc: Exception) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/boom")
    async def boom() -> None:
        raise exc

    return app


@pytest.mark.parametrize(
    ("exc", "what"),
    [
        (OperationalError("SELECT 1", None, Exception("connection refused")), "database"),
        (socket.gaierror(-3, "Temporary failure in name resolution"), "database"),
        (ConnectionRefusedError("refused"), "database"),
        (ConnectionResetError("reset"), "database"),
        (RedisConnectionError("Connection closed by server."), "message broker"),
    ],
)
def test_an_unreachable_dependency_is_503_with_a_retry(exc: Exception, what: str) -> None:
    with TestClient(_app_that_raises(exc), raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 503
    body = response.json()["error"]
    assert body["code"] == "DEPENDENCY_UNAVAILABLE"
    assert what in body["message"]
    # A retry that is part of the outage is not a retry.
    assert response.headers["retry-after"] == "5"


def test_a_statement_that_was_refused_is_still_the_callers_problem() -> None:
    """IntegrityError is not a dependency being away -- it is a row that broke
    a constraint, and telling a client to come back would have it retry
    something that will fail identically for ever."""
    exc = IntegrityError("INSERT", None, Exception("duplicate key"))
    with TestClient(_app_that_raises(exc), raise_server_exceptions=False) as client:
        response = client.get("/boom")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL"


def test_an_ordinary_bug_is_still_500() -> None:
    """The reason the handlers are named types rather than a broad OSError:
    503 on a genuine fault would hide it behind a retry loop."""
    with TestClient(_app_that_raises(ValueError("a real bug")), raise_server_exceptions=False) as c:
        response = c.get("/boom")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL"
