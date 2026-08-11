from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    access_token: str = Field(serialization_alias="accessToken")
    token_type: str = Field(default="bearer", serialization_alias="tokenType")
    expires_in: int = Field(serialization_alias="expiresIn")


class MeResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: str
    email: str
    name: str
    org_role: str = Field(serialization_alias="orgRole")
    organization_id: str = Field(serialization_alias="organizationId")
