from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PoolRuntime(StrEnum):
    DOCKER = "docker"
    KUBERNETES = "kubernetes"


class PoolCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=255)
    runtime: PoolRuntime
    config: dict[str, Any] = Field(default_factory=dict)
    region: str | None = Field(default=None, max_length=100)
    max_generators: int = Field(ge=1, alias="maxGenerators")
    max_vus_per_generator: int = Field(ge=1, alias="maxVusPerGenerator")


class PoolUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = Field(default=None, min_length=1, max_length=255)
    config: dict[str, Any] | None = None
    region: str | None = Field(default=None, max_length=100)
    max_generators: int | None = Field(default=None, ge=1, alias="maxGenerators")
    max_vus_per_generator: int | None = Field(default=None, ge=1, alias="maxVusPerGenerator")


class PoolResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: uuid.UUID
    name: str
    runtime: str
    config: dict[str, Any]
    region: str | None
    max_generators: int = Field(serialization_alias="maxGenerators")
    max_vus_per_generator: int = Field(serialization_alias="maxVusPerGenerator")
    # Stated rather than left to the client to multiply, because preflight and
    # the interface must agree on what a pool can supply.
    capacity: int
    supported_engines: list[str] = Field(serialization_alias="supportedEngines")
    status: str
    created_at: datetime = Field(serialization_alias="createdAt")


class ProbeResult(BaseModel):
    """Whether a pool could actually run a generator right now.

    Reported rather than raised: an operator fixing a pool wants the reason,
    and a diagnostic that throws is a diagnostic that tells them less.
    """

    ok: bool
    detail: str
