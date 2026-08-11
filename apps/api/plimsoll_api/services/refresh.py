from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.config import get_settings
from plimsoll_api.repositories import refresh_tokens as repo


class RefreshRejected(Exception):
    """The token was unknown, already consumed, revoked, or expired."""


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def issue_family(session: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID) -> str:
    raw = secrets.token_urlsafe(48)
    family_id = uuid.uuid4()
    expires_at = datetime.now(UTC) + timedelta(seconds=get_settings().refresh_token_ttl_seconds)
    await repo.insert_family(session, family_id, org_id, user_id, _hash(raw), expires_at)
    await repo.record_history(session, family_id, org_id, _hash(raw))
    return raw


async def rotate(session: AsyncSession, raw_token: str) -> tuple[str, uuid.UUID, uuid.UUID]:
    token_hash = _hash(raw_token)
    row = await repo.find_by_hash(session, token_hash)

    if row is None:
        # Not the live token. If we have ever seen it, it is a replay: kill the family.
        await repo.revoke_family_containing(session, token_hash)
        raise RefreshRejected("Unknown or already-consumed refresh token.")

    if row.revoked_at is not None:
        raise RefreshRejected("This token family has been revoked.")
    if row.expires_at <= datetime.now(UTC):
        raise RefreshRejected("Refresh token expired.")

    new_raw = secrets.token_urlsafe(48)
    await repo.replace_hash(session, row.id, _hash(new_raw))
    await repo.record_history(session, row.id, row.organization_id, _hash(new_raw))
    return new_raw, row.user_id, row.organization_id
