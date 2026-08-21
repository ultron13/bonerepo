"""Bringing a tenant into being."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# The slug is what somebody types to reach their identity provider, so it has
# to survive a URL and be readable in one.
SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class OrganizationCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=63)
    admin_email: EmailStr = Field(alias="adminEmail")
    admin_name: str = Field(min_length=1, max_length=255, alias="adminName")

    @field_validator("slug")
    @classmethod
    def _readable_in_a_url(cls, value: str) -> str:
        lowered = value.strip().lower()
        if not SLUG.match(lowered):
            raise ValueError(
                "A slug is lower-case letters, digits and hyphens, and neither "
                "starts nor ends with a hyphen."
            )
        return lowered


class OrganizationCreated(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: str
    name: str
    slug: str
    admin_email: str = Field(serialization_alias="adminEmail")
    # Shown once. There is no path that returns it again.
    admin_temporary_password: str = Field(serialization_alias="adminTemporaryPassword")
