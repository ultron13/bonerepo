"""Where this system sends what happened."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# The families a subscription can ask for. Named rather than free text: a typo
# in an event name would otherwise produce a subscription that is configured,
# looks right, and never fires.
EventName = Literal["audit.*", "run.completed", "run.failed", "run.sla_breached"]


class WebhookInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    url: str = Field(min_length=1, max_length=2048)
    events: list[EventName] = Field(min_length=1)
    # Optional: generated when absent, because a secret somebody has to invent
    # is a secret somebody will invent badly.
    secret: str | None = Field(default=None, min_length=16, max_length=256)

    @field_validator("url")
    @classmethod
    def _shape(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("A webhook URL must be https://.")
        return value


class Webhook(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: str
    url: str
    events: list[str]
    status: str
    created_at: datetime = Field(serialization_alias="createdAt")


class WebhookCreated(Webhook):
    """The one response carrying the secret. Shown once, like an API key."""

    secret: str
