"""The audit trail, as a reader sees it."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AuditEntry(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: uuid.UUID
    # Who: a user, or an API key acting on its behalf. Both may be absent for
    # an action the system took on nobody's instruction.
    user_id: uuid.UUID | None = Field(serialization_alias="userId")
    api_key_id: uuid.UUID | None = Field(serialization_alias="apiKeyId")
    action: str
    entity_type: str | None = Field(serialization_alias="entityType")
    entity_id: uuid.UUID | None = Field(serialization_alias="entityId")
    ip_address: str | None = Field(serialization_alias="ipAddress")
    metadata: dict[str, Any] | None = None
    created_at: datetime = Field(serialization_alias="createdAt")
