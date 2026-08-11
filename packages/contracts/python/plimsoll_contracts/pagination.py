"""Opaque cursor pagination. Offsets skip or duplicate rows under concurrent
inserts, which on a busy control plane is always."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200


def encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding)
        decoded = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("Malformed cursor.") from exc
    if not isinstance(decoded, dict):
        raise ValueError("Malformed cursor.")
    return decoded


class Page[T](BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    items: list[T]
    next_cursor: str | None = Field(default=None, serialization_alias="nextCursor")
