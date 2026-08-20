"""The agent wire protocol, defined once and imported by both ends.

The agent is the only component outside the control plane's trust boundary, so
every frame it sends is parsed into one of these before anything acts on it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentState(StrEnum):
    STARTING = "STARTING"
    FETCHING = "FETCHING"
    READY = "READY"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Command(StrEnum):
    WAIT = "WAIT"
    START = "START"
    STOP = "STOP"
    CANCEL = "CANCEL"


class Register(BaseModel):
    type: Literal["register"] = "register"
    ordinal: int
    version: str


class StateReport(BaseModel):
    type: Literal["state"] = "state"
    state: AgentState
    reason: str | None = None


class Heartbeat(BaseModel):
    type: Literal["heartbeat"] = "heartbeat"


class Registered(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    type: Literal["registered"] = "registered"
    desired_state: str = Field(serialization_alias="desiredState")
    command: Command
    # Everything the agent needs that is not in the bundle.
    assigned_users: int = Field(serialization_alias="assignedUsers")
    duration_seconds: int = Field(serialization_alias="durationSeconds")
    ramp_up_seconds: int = Field(serialization_alias="rampUpSeconds")
    plan_path: str = Field(default="", serialization_alias="planPath")
    # Signed per registration rather than stored: a presigned URL expires and
    # configuration does not.
    bundle_url: str = Field(default="", serialization_alias="bundleUrl")
    bundle_sha256: str = Field(default="", serialization_alias="bundleSha256")
    allowlist: list[str] = Field(default_factory=list)
    # Names to values, for the variables the plan references. In memory only.
    variables: dict[str, str] = Field(default_factory=dict)


class HeartbeatAck(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    type: Literal["heartbeat_ack"] = "heartbeat_ack"
    desired_state: str = Field(serialization_alias="desiredState")
    command: Command


class CommandFrame(BaseModel):
    type: Literal["command"] = "command"
    command: Command


class Accepted(BaseModel):
    type: Literal["accepted"] = "accepted"


class MetricsFrame(BaseModel):
    """Closed windows, on their way to the ingestion stream.

    The agent has no broker credential and must not gain one: it reaches the
    control plane over the socket it already holds, and the API publishes on
    its behalf with the organisation taken from the token.
    """

    type: Literal["metrics"] = "metrics"
    windows: list[dict[str, str]]


class ErrorsFrame(BaseModel):
    """Failures already grouped by the generator that saw them."""

    type: Literal["errors"] = "errors"
    groups: list[dict[str, str]]


class ArtifactUrlRequest(BaseModel):
    type: Literal["artifact_url_request"] = "artifact_url_request"
    name: str


class ArtifactUrl(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    type: Literal["artifact_url"] = "artifact_url"
    name: str
    url: str
