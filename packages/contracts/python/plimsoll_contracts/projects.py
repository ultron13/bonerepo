from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=255)
    project_key: str = Field(
        min_length=1, max_length=50, pattern=r"^[A-Z][A-Z0-9_]*$", alias="projectKey"
    )
    description: str | None = None
    environment: str | None = Field(default=None, max_length=50)
    tags: list[str] = Field(default_factory=list)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    environment: str | None = Field(default=None, max_length=50)
    tags: list[str] | None = None


class ProjectResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: uuid.UUID
    name: str
    project_key: str = Field(serialization_alias="projectKey")
    description: str | None
    environment: str | None
    status: str
    tags: list[str]
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")
