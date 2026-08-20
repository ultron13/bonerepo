"""API keys, as a caller sees them."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ApiKeyCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=255)
    # What the key may do. A key holds exactly these and nothing its creator's
    # role would otherwise add.
    scopes: list[str] = Field(min_length=1)
    expires_in_days: int | None = Field(default=None, ge=0, le=3650, alias="expiresInDays")


class ApiKeyResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: uuid.UUID
    name: str
    # Enough to recognise the key in a list, far too little to use.
    prefix: str
    scopes: list[str]
    last_used_at: datetime | None = Field(serialization_alias="lastUsedAt")
    expires_at: datetime | None = Field(serialization_alias="expiresAt")
    created_at: datetime = Field(serialization_alias="createdAt")


class ApiKeyCreated(ApiKeyResponse):
    """The one and only time the secret is returned.

    It is stored as a hash, so this response cannot be reproduced: a lost key
    is replaced, never recovered.
    """

    secret: str
