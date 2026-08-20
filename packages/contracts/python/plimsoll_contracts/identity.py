"""Single sign-on configuration for an organisation."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IdentityProviderInput(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # An issuer and nothing else. Every endpoint below it can move, and a
    # provider that publishes discovery is the only one that knows where they
    # are now.
    issuer: str = Field(min_length=1, max_length=512)
    client_id: str = Field(min_length=1, max_length=512, alias="clientId")
    client_secret: str = Field(min_length=1, max_length=1024, alias="clientSecret")
    groups_claim: str = Field(default="groups", max_length=128, alias="groupsClaim")
    admin_group: str | None = Field(default=None, max_length=256, alias="adminGroup")
    # No default. A provider that has not said which domains it speaks for
    # should not be able to create an account for any address at all.
    allowed_domains: list[str] = Field(alias="allowedDomains", min_length=1)

    @field_validator("issuer")
    @classmethod
    def _must_be_a_url(cls, value: str) -> str:
        """Shape only. Whether plain HTTP is acceptable is a property of the
        deployment rather than of the request, so it is decided by the API
        where the settings are, not here."""
        if not value.startswith(("https://", "http://")):
            raise ValueError("The issuer must be an http:// or https:// URL.")
        return value.rstrip("/")

    @field_validator("allowed_domains")
    @classmethod
    def _tidy_domains(cls, value: list[str]) -> list[str]:
        return sorted({entry.strip().lstrip("@").lower() for entry in value if entry.strip()})


class IdentityProvider(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: str
    issuer: str
    client_id: str = Field(serialization_alias="clientId")
    groups_claim: str = Field(serialization_alias="groupsClaim")
    admin_group: str | None = Field(serialization_alias="adminGroup")
    allowed_domains: list[str] = Field(serialization_alias="allowedDomains")
    enabled: bool
    created_at: datetime = Field(serialization_alias="createdAt")
    # The sign-in URL to publish to people in this organisation.
    start_url: str = Field(serialization_alias="startUrl")
