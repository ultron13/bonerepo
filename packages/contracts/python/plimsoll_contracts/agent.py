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
