from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from plimsoll_api.config import get_settings

ALGORITHM = "HS256"


class TokenError(Exception):
    """Raised when a token is absent, malformed, tampered with, or expired."""


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: str


def issue_access_token(user_id: uuid.UUID, org_id: uuid.UUID, role: str) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "org": str(org_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(seconds=settings.access_token_ttl_seconds),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> AccessClaims:
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    try:
        return AccessClaims(
            user_id=uuid.UUID(payload["sub"]),
            organization_id=uuid.UUID(payload["org"]),
            role=payload["role"],
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise TokenError("The token is missing a required claim.") from exc


AGENT_AUDIENCE = "agent"


@dataclass(frozen=True)
class AgentClaims:
    run_id: uuid.UUID
    ordinal: int
    organization_id: uuid.UUID


def issue_agent_token(
    run_id: uuid.UUID, *, ordinal: int, org_id: uuid.UUID, ttl_seconds: int
) -> str:
    """Scoped to one run and one ordinal, and expiring with the run.

    There is no long-lived registration secret on a generator because a
    generator does not outlive its run -- so neither does its credential.

    The `aud` claim is what keeps the two token families apart in both
    directions: `decode_access_token` passes no audience, and PyJWT refuses a
    token that carries one, so an agent token opens no ordinary API route.
    """
    now = datetime.now(UTC)
    payload = {
        "sub": str(run_id),
        "ordinal": ordinal,
        "org": str(org_id),
        "aud": AGENT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(payload, get_settings().jwt_secret, algorithm=ALGORITHM)


def decode_agent_token(token: str) -> AgentClaims:
    try:
        payload = jwt.decode(
            token,
            get_settings().jwt_secret,
            algorithms=[ALGORITHM],
            audience=AGENT_AUDIENCE,
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    try:
        return AgentClaims(
            run_id=uuid.UUID(payload["sub"]),
            ordinal=int(payload["ordinal"]),
            organization_id=uuid.UUID(payload["org"]),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise TokenError("The token is missing a required claim.") from exc
