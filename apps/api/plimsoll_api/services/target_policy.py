"""The control that separates a load test from an attack.

An empty allowlist permits nothing. There is no implicit permit-all state and
no first-run convenience exception -- ADR-0007 rejected warn-and-audit, and a
policy that starts open is the same thing under another name.
"""

from __future__ import annotations

import ipaddress
import re
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.errors import PlimsollError
from plimsoll_api.repositories import target_policy as repo
from plimsoll_api.security.tokens import AccessClaims
from plimsoll_api.services import audit
from plimsoll_contracts.errors import ErrorCode

HOSTNAME = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$")

# Blocked from generator containers regardless of allowlist, so an entry that
# would permit one is refused at write time rather than surprising a run.
BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fe80::/10"),
]
BLOCKED_NAMES = {"localhost", "metadata.google.internal"}


def _refuse(entry: str, reason: str) -> None:
    raise PlimsollError(
        ErrorCode.VALIDATION_FAILED,
        f"Allowlist entry {entry!r} is not permitted: {reason}",
        {"entry": entry},
    )


def _is_cidr(value: str) -> bool:
    if "/" not in value:
        return False
    try:
        ipaddress.ip_network(value, strict=False)
    except ValueError:
        return False
    return True


def _is_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def validate_entries(entries: list[str]) -> None:
    for entry in entries:
        candidate = entry.strip().lower()
        if not candidate or candidate != entry.lower():
            _refuse(entry, "an entry must not be empty or padded with whitespace.")

        if _is_cidr(candidate):
            network = ipaddress.ip_network(candidate, strict=False)
            if network.prefixlen == 0:
                _refuse(entry, "it permits every address.")
            if any(network.overlaps(blocked) for blocked in BLOCKED_NETWORKS):
                _refuse(entry, "it overlaps loopback or link-local addresses.")
            continue

        if "/" in candidate:
            _refuse(entry, "it looks like a URL or path; use a hostname, suffix, or CIDR.")

        bare = candidate.removeprefix(".")
        if bare in BLOCKED_NAMES:
            _refuse(entry, "loopback and metadata hosts are never reachable from a generator.")
        if _is_address(bare):
            address = ipaddress.ip_address(bare)
            if any(address in blocked for blocked in BLOCKED_NETWORKS):
                _refuse(entry, "loopback and link-local addresses are never permitted.")
            continue
        if not HOSTNAME.match(bare):
            _refuse(entry, "it is neither a hostname, a domain suffix, nor a CIDR.")


def matches_allowlist(host: str, entries: list[str]) -> bool:
    """Matched on the host as written in the plan.

    No DNS resolution here: resolving at admission would open exactly the
    repoint window the agent's second check exists to close.
    """
    candidate = host.strip().lower()
    if not candidate:
        return False

    for entry in entries:
        rule = entry.strip().lower()
        if _is_cidr(rule):
            if _is_address(candidate) and ipaddress.ip_address(candidate) in ipaddress.ip_network(
                rule, strict=False
            ):
                return True
        elif rule.startswith("."):
            suffix = rule.removeprefix(".")
            if candidate == suffix or candidate.endswith(f".{suffix}"):
                return True
        elif candidate == rule:
            return True
    return False


async def current_policy(session: AsyncSession) -> sa.Row[Any] | None:
    return await repo.current(session)


async def replace(
    session: AsyncSession, principal: AccessClaims, allowlist: list[str]
) -> sa.Row[Any]:
    validate_entries(allowlist)
    row = await repo.insert_next_version(
        session,
        org_id=principal.organization_id,
        created_by=principal.user_id,
        allowlist=allowlist,
    )
    await audit.record(
        session,
        principal=principal,
        action="target_policy.updated",
        entity_type="target_policy",
        entity_id=uuid.UUID(str(row.id)),
        metadata={"version": row.version, "entries": len(allowlist)},
    )
    return row
