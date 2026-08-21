from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TargetPolicyUpdate(BaseModel):
    # An empty list is valid and permits no runs. That is the safe state, not a
    # configuration error.
    allowlist: list[str]


class TargetPolicyResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    version: int
    allowlist: list[str]
    created_at: datetime | None = Field(default=None, serialization_alias="createdAt")
