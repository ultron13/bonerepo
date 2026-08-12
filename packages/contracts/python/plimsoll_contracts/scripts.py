from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

ALLOWED_SCHEMES = ("http://", "https://", "ssh://", "git@")


class ScriptRepoCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=255)
    repo_url: str = Field(min_length=1, alias="repoUrl")
    plan_path: str = Field(min_length=1, alias="planPath")
    default_ref: str = Field(default="main", max_length=255, alias="defaultRef")
    credential_id: uuid.UUID | None = Field(default=None, alias="credentialId")

    @field_validator("repo_url")
    @classmethod
    def _supported_scheme(cls, value: str) -> str:
        if not value.startswith(ALLOWED_SCHEMES):
            raise ValueError(
                "a repository URL must be http, https, or ssh; "
                "the control plane does not fetch local paths"
            )
        return value

    @field_validator("plan_path")
    @classmethod
    def _inside_the_repository(cls, value: str) -> str:
        if value.startswith("/") or ".." in value.split("/"):
            raise ValueError("a plan path is relative to the repository root")
        return value


class ScriptRepoUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str | None = Field(default=None, min_length=1, max_length=255)
    plan_path: str | None = Field(default=None, min_length=1, alias="planPath")
    default_ref: str | None = Field(default=None, max_length=255, alias="defaultRef")
    credential_id: uuid.UUID | None = Field(default=None, alias="credentialId")


class ScriptRepoResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: uuid.UUID
    project_id: uuid.UUID = Field(serialization_alias="projectId")
    name: str
    engine: str
    repo_url: str = Field(serialization_alias="repoUrl")
    default_ref: str = Field(serialization_alias="defaultRef")
    plan_path: str = Field(serialization_alias="planPath")
    credential_id: uuid.UUID | None = Field(serialization_alias="credentialId")
    status: str
    created_at: datetime = Field(serialization_alias="createdAt")
    updated_at: datetime = Field(serialization_alias="updatedAt")


class FindingSeverity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class Finding(BaseModel):
    code: str
    severity: FindingSeverity
    message: str
    location: str | None = None


class PlanTargetResponse(BaseModel):
    scheme: str
    host: str
    port: int | None


class PlanSummaryResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    thread_groups: list[str] = Field(serialization_alias="threadGroups")
    transaction_controllers: list[str] = Field(serialization_alias="transactionControllers")
    timers: list[str]
    variables: list[str]
    data_files: list[str] = Field(serialization_alias="dataFiles")
    targets: list[PlanTargetResponse]


class PluginSummary(BaseModel):
    id: str
    version: str
    sha256: str


class VerifyReport(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    ok: bool
    commit_sha: str | None = Field(default=None, serialization_alias="commitSha")
    ref: str
    findings: list[Finding] = Field(default_factory=list)
    plan: PlanSummaryResponse | None = None
    plugins: list[PluginSummary] = Field(default_factory=list)


class ScriptVersionCreate(BaseModel):
    # Absent means the repository's default_ref.
    ref: str | None = Field(default=None, max_length=255)


class ScriptVersionResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: uuid.UUID
    script_repo_id: uuid.UUID = Field(serialization_alias="scriptRepoId")
    commit_sha: str = Field(serialization_alias="commitSha")
    plan_path: str = Field(serialization_alias="planPath")
    checksum: str | None
    resolved_at: datetime = Field(serialization_alias="resolvedAt")
