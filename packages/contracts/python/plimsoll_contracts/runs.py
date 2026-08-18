from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    ALLOCATING = "ALLOCATING"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_RUN_STATUSES = frozenset({RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED})


class GeneratorStatus(StrEnum):
    PENDING = "PENDING"
    PROVISIONED = "PROVISIONED"
    REGISTERED = "REGISTERED"
    FETCHING = "FETCHING"
    READY = "READY"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    # No heartbeat within the timeout. Capacity loss, never rescheduled.
    LOST = "LOST"


TERMINAL_GENERATOR_STATUSES = frozenset(
    {GeneratorStatus.COMPLETED, GeneratorStatus.FAILED, GeneratorStatus.LOST}
)


class GeneratorView(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    ordinal: int
    status: GeneratorStatus
    assigned_users: int = Field(serialization_alias="assignedUsers")
    last_heartbeat: datetime | None = Field(serialization_alias="lastHeartbeat")


class RunResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: uuid.UUID
    project_id: uuid.UUID = Field(serialization_alias="projectId")
    performance_test_id: uuid.UUID = Field(serialization_alias="performanceTestId")
    run_number: int = Field(serialization_alias="runNumber")
    status: RunStatus
    trigger_source: str = Field(serialization_alias="triggerSource")
    degraded: bool
    started_at: datetime | None = Field(serialization_alias="startedAt")
    ended_at: datetime | None = Field(serialization_alias="endedAt")
    created_at: datetime = Field(serialization_alias="createdAt")
    configuration_snapshot: dict[str, Any] = Field(serialization_alias="configurationSnapshot")
    summary: dict[str, Any] | None = None


class RunStatusResponse(BaseModel):
    """Deliberately small: this is the endpoint a client polls."""

    model_config = ConfigDict(serialize_by_alias=True)

    id: uuid.UUID
    status: RunStatus
    degraded: bool
    started_at: datetime | None = Field(serialization_alias="startedAt")
    ended_at: datetime | None = Field(serialization_alias="endedAt")
    generators: list[GeneratorView]
