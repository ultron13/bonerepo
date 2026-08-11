"""A client must never learn of a write before that write has committed.

FastAPI closes a dependency that uses `yield` *after* the response has been
sent, so a request-scoped transaction owned by one commits too late: a client
acting on the response immediately can beat the commit. Login is where that
first bites -- the refresh cookie it hands back names a row the very next
request looks up -- so the ordering is asserted at the moment the response
headers leave, against a second connection that can only see committed rows.
"""

import hashlib
from http.cookies import SimpleCookie

import httpx
import pytest
import sqlalchemy as sa
from starlette.types import Message, Receive, Scope, Send

from plimsoll_api.db.session import session_for_org
from plimsoll_api.main import create_app

pytestmark = pytest.mark.integration

ADMIN = {"email": "admin@demo.plimsoll.dev", "password": "plimsoll-demo-password"}


async def _refresh_family_is_committed(raw_token: str) -> bool:
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    async with session_for_org(None) as session:
        organization_id = await session.scalar(
            sa.text("SELECT auth_org_for_refresh(:hash)"), {"hash": token_hash}
        )
    return organization_id is not None


def _refresh_cookie(headers: list[tuple[bytes, bytes]]) -> str:
    jar: SimpleCookie = SimpleCookie()
    for name, value in headers:
        if name.decode().lower() == "set-cookie":
            jar.load(value.decode())
    return jar["plimsoll_refresh"].value


async def test_the_refresh_family_is_committed_before_the_cookie_is_sent() -> None:
    committed_when_headers_left: list[bool] = []
    app = create_app()

    async def probe(scope: Scope, receive: Receive, send: Send) -> None:
        async def probing_send(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] == 200:
                token = _refresh_cookie(message["headers"])
                committed_when_headers_left.append(await _refresh_family_is_committed(token))
            await send(message)

        await app(scope, receive, probing_send)

    transport = httpx.ASGITransport(app=probe)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post("/api/v1/auth/login", json=ADMIN)

    assert response.status_code == 200
    assert committed_when_headers_left == [True]
