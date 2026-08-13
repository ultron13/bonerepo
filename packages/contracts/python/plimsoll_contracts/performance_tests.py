from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SlaMetric(StrEnum):
    P50 = "p50"
    P90 = "p90"
    P95 = "p95"
    P99 = "p99"
    AVG = "avg"
    ERROR_RATE = "error_rate"
    THROUGHPUT = "throughput"


class SlaOperator(StrEnum):
    LT = "lt"
    LTE = "lte"
    GT = "gt"
    GTE = "gte"


class SlaSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class WorkloadSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    virtual_users: int = Field(ge=1, alias="virtualUsers", serialization_alias="virtualUsers")
    duration_seconds: int = Field(
        ge=1, alias="durationSeconds", serialization_alias="durationSeconds"
    )
    ramp_up_seconds: int = Field(
        default=0, ge=0, alias="rampUpSeconds", serialization_alias="rampUpSeconds"
    )
    generator_pool_id: uuid.UUID = Field(
        alias="generatorPoolId", serialization_alias="generatorPoolId"
    )

    @model_validator(mode="after")
    def _ramp_fits(self) -> WorkloadSpec:
        if self.ramp_up_seconds > self.duration_seconds:
            raise ValueError("rampUpSeconds cannot exceed durationSeconds")
        return self


class TestPlanSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    script_repo_id: uuid.UUID = Field(alias="scriptRepoId", serialization_alias="scriptRepoId")
    # NULL means the repository's default ref; a SHA pins exactly.
    pinned_ref: str | None = Field(
        default=None, max_length=255, alias="pinnedRef", serialization_alias="pinnedRef"
    )
    virtual_users: int = Field(
        default=1, ge=1, alias="virtualUsers", serialization_alias="virtualUsers"
    )
    execution_order: int = Field(
        default=1, ge=1, alias="executionOrder", serialization_alias="executionOrder"
    )


class SlaRuleSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

    name: str = Field(min_length=1, max_length=255)
    metric: SlaMetric
    # A transaction name, or null for the run as a whole.
    entity: str | None = None
    operator: SlaOperator
    threshold: float
    unit: str | None = Field(default=None, max_length=50)
    severity: SlaSeverity = SlaSeverity.ERROR
    enabled: bool = True


class TestCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    configuration: WorkloadSpec
    plans: list[TestPlanSpec] = Field(min_length=1)
    sla_rules: list[SlaRuleSpec] = Field(default_factory=list, alias="slaRules")
    tags: list[str] = Field(default_factory=list)


class TestUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # Required: an edit without the version it is based on cannot be checked
    # for a concurrent change.
    version: int
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    configuration: WorkloadSpec | None = None
    plans: list[TestPlanSpec] | None = Field(default=None, min_length=1)
    sla_rules: list[SlaRuleSpec] | None = Field(default=None, alias="slaRules")
    tags: list[str] | None = None


class TestResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: uuid.UUID
    project_id: uuid.UUID = Field(serialization_alias="projectId")
    name: str
    description: str | None
    status: str
    configuration: WorkloadSpec
    plans: list[TestPlanSpec]
    sla_rules: list[SlaRuleSpec] = Field(serialization_alias="slaRules")
    tags: list[str]
    version: int
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")
