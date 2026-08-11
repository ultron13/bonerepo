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
