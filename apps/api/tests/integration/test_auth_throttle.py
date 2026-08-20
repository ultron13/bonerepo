"""Slowing down someone guessing passwords, without locking anyone out.

A deployment ships with a seeded administrator whose address is in the README.
Unlimited attempts against that is the cheapest attack available, and the
throttle that stops it must not become a way to lock a colleague out.
"""

import os
import uuid
from collections.abc import Iterator

import httpx
import pytest
import redis

from tests.integration.conftest import ADMIN, API_URL

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clean_counters() -> Iterator[None]:
    """The throttle's state is shared by everything that signs in.

    These tests deliberately exhaust it, and the per-address counter refuses a
    correct password as well as a wrong one -- that is the point of it. Leaving
    it exhausted would refuse every later test in the suite, so it is cleared
    on the way in and on the way out.
    """
    _forget()
    yield
    _forget()


def _forget() -> None:
    client = redis.Redis.from_url(os.environ["PLIMSOLL_REDIS_URL"], decode_responses=True)
    try:
        keys = list(client.scan_iter(match="auth:fail:*"))
        if keys:
            client.delete(*keys)
    finally:
        client.close()


def _client() -> httpx.Client:
    return httpx.Client(base_url=API_URL, timeout=30)


def _guess(client: httpx.Client, email: str) -> httpx.Response:
    return client.post("/api/v1/auth/login", json={"email": email, "password": "wrong-guess"})


def test_repeated_guesses_are_eventually_refused() -> None:
    email = f"nobody-{uuid.uuid4().hex[:10]}@example.com"
    with _client() as client:
        codes = [_guess(client, email).status_code for _ in range(15)]

    assert 429 in codes, codes
    # It refuses only after letting a few through: a throttle that trips on the
    # first typo is a support ticket, not a control.
    assert codes[0] == 401
    assert codes.index(429) >= 3, codes


def test_a_refusal_says_when_to_come_back() -> None:
    email = f"nobody-{uuid.uuid4().hex[:10]}@example.com"
    with _client() as client:
        refused = next(
            (response for _ in range(15) if (response := _guess(client, email)).status_code == 429),
            None,
        )
    assert refused is not None
    assert int(refused.headers["retry-after"]) > 0


def test_guessing_one_account_does_not_lock_another_out() -> None:
    """The lockout question. Throttling that a stranger can aim at a colleague
    is a denial of service wearing a security badge."""
    victim = f"victim-{uuid.uuid4().hex[:10]}@example.com"
    with _client() as client:
        for _ in range(15):
            _guess(client, victim)

        # A different account, correct credentials, same source address.
        signed_in = client.post("/api/v1/auth/login", json=ADMIN)
    assert signed_in.status_code == 200, signed_in.text


def test_a_correct_password_is_never_throttled() -> None:
    """Only failures count. Someone signing in repeatedly is using the product,
    not attacking it."""
    with _client() as client:
        codes = [client.post("/api/v1/auth/login", json=ADMIN).status_code for _ in range(12)]
    assert set(codes) == {200}, codes


def test_signing_in_clears_the_count_against_that_account() -> None:
    """A user who mistypes a few times and then gets it right starts clean, so
    their next mistake does not land them at the limit."""
    with _client() as client:
        for _ in range(3):
            _guess(client, ADMIN["email"])
        assert client.post("/api/v1/auth/login", json=ADMIN).status_code == 200
        # Well under the limit from a clean count.
        assert _guess(client, ADMIN["email"]).status_code == 401
        assert client.post("/api/v1/auth/login", json=ADMIN).status_code == 200


def test_a_source_that_keeps_guessing_is_refused_outright() -> None:
    """The credential-stuffing case: many accounts, one source.

    Blocking the source has to refuse a correct password too, or working
    through a list until one lands would still succeed. It is generous and
    short-lived for the same reason -- an office behind one address must not be
    shut out by one careless colleague.
    """
    with _client() as client:
        for _ in range(45):
            _guess(client, f"someone-{uuid.uuid4().hex[:8]}@example.com")
        refused = client.post("/api/v1/auth/login", json=ADMIN)
    assert refused.status_code == 429, refused.text
