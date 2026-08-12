"""Keyset paging over (created_at, id), shared by every collection endpoint.

The cursor encoding lives in the contracts package because clients see it; this
is the server half -- decode a cursor into a keyset position, and turn one
over-fetched row into the next cursor.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any

from plimsoll_api.errors import PlimsollError
from plimsoll_contracts.errors import ErrorCode
from plimsoll_contracts.pagination import Page, decode_cursor, encode_cursor

Position = tuple[datetime, uuid.UUID]


def position_from(cursor: str | None) -> Position | None:
    if cursor is None:
        return None
    try:
        payload = decode_cursor(cursor)
        return datetime.fromisoformat(payload["createdAt"]), uuid.UUID(payload["id"])
    except (ValueError, KeyError, TypeError) as exc:
        raise PlimsollError(ErrorCode.VALIDATION_FAILED, "The cursor is malformed.") from exc


def page_of[T](rows: Sequence[Any], limit: int, present: Callable[[Any], T]) -> Page[T]:
    """`rows` holds up to limit + 1 rows; the extra one proves there is more."""
    visible = list(rows[:limit])
    next_cursor = None
    if len(rows) > limit and visible:
        last = visible[-1]
        next_cursor = encode_cursor({"createdAt": last.created_at.isoformat(), "id": str(last.id)})
    return Page(items=[present(row) for row in visible], next_cursor=next_cursor)
