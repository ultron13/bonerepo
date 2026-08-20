"""Slowing down someone guessing passwords.

Only failures are counted. Someone signing in repeatedly is using the product,
not attacking it, and throttling them would be a support ticket rather than a
control. A successful sign-in clears the count against that account, so a user
who mistypes twice and then gets it right starts clean.

Two counters, because they answer different questions:

* per account -- somebody is working through passwords for one person
* per source address -- somebody is working through accounts from one place

The per-account counter is the one that could be turned into a lock-out: a
stranger hammering a colleague's address could exhaust it. It is therefore
generous, short-lived, and cleared by any success, and the per-address counter
is the one doing the real work. Locking an account outright would hand an
attacker a denial of service wearing a security badge.
"""

from __future__ import annotations

import hashlib

from fastapi import Request

from plimsoll_api.config import get_settings
from plimsoll_api.errors import PlimsollError
from plimsoll_api.messaging import get_bus
from plimsoll_contracts.errors import ErrorCode


def _identifier_key(identifier: str) -> str:
    # Hashed: this lands in a shared cache, and an address is personal data
    # that the throttle has no reason to store in the clear.
    digest = hashlib.sha256(identifier.strip().lower().encode()).hexdigest()[:32]
    return f"auth:fail:id:{digest}"


def _address_key(address: str) -> str:
    return f"auth:fail:ip:{address}"


def client_address(request: Request) -> str:
    """Where the request came from.

    A forwarded header is trusted only when the deployment says it sits behind
    a proxy that sets one. Trusting it by default would let anyone claim a
    fresh address per attempt and walk straight through the throttle.
    """
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def _count(key: str) -> int:
    client = get_bus().client
    window = get_settings().auth_failure_window_seconds
    async with client.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, window, nx=True)
        counted, _ = await pipe.execute()
    return int(counted)


async def _current(key: str) -> int:
    value = await get_bus().client.get(key)
    return int(value) if value else 0


async def refuse_if_throttled(identifier: str, address: str) -> None:
    """Raise before the password is checked.

    Checking first would make the throttle a timing oracle: a refused request
    that took as long as a real check would tell an attacker the account exists.
    """
    settings = get_settings()
    for key, limit in (
        (_identifier_key(identifier), settings.auth_failure_limit),
        (_address_key(address), settings.auth_failure_limit_per_address),
    ):
        if await _current(key) >= limit:
            raise PlimsollError(
                ErrorCode.RATE_LIMITED,
                "Too many failed sign-in attempts. Try again shortly.",
                headers={"Retry-After": str(settings.auth_failure_window_seconds)},
            )


async def record_failure(identifier: str, address: str) -> None:
    await _count(_identifier_key(identifier))
    await _count(_address_key(address))


async def record_success(identifier: str) -> None:
    """Clears the account's count, and only the account's.

    The address keeps its count: a success from a source that has been guessing
    at other accounts says nothing about the guessing.
    """
    await get_bus().client.delete(_identifier_key(identifier))
