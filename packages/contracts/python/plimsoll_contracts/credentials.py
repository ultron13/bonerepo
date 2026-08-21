from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class CredentialKind(StrEnum):
    GIT_TOKEN = "GIT_TOKEN"  # noqa: S105 - the name of a kind, not a secret
    GIT_SSH_KEY = "GIT_SSH_KEY"
    VARIABLE = "VARIABLE"


class CredentialCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    kind: CredentialKind
    # Write-only. No response model carries this field, and adding one would
    # break the guarantee that secrets are never returned by the API.
    secret: str = Field(min_length=1)


class CredentialResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: uuid.UUID
    name: str
    kind: CredentialKind
    created_at: datetime = Field(serialization_alias="createdAt")
