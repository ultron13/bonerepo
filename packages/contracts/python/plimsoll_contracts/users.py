"""The people in an organisation, and what each of them may do."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# The roles v0.1 issues. A Literal rather than free text, because a typo in a
# role name would otherwise create a user who holds nothing and looks fine.
OrgRole = Literal["ORG_ADMIN", "PERFORMANCE_ENGINEER", "TESTER", "VIEWER"]
UserStatus = Literal["ACTIVE", "SUSPENDED"]


class UserInvite(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=255)
    org_role: OrgRole = Field(default="VIEWER", alias="orgRole")

    model_config = ConfigDict(populate_by_name=True)


class UserUpdate(BaseModel):
    org_role: OrgRole = Field(alias="orgRole")

    model_config = ConfigDict(populate_by_name=True)


class User(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: str
    email: str
    name: str
    org_role: OrgRole = Field(serialization_alias="orgRole")
    status: UserStatus
    created_at: datetime = Field(serialization_alias="createdAt")


class UserInvited(User):
    """The one response that carries a secret.

    Shown once, like an API key. There is no mail transport in v0.1, so the
    administrator passes it on; the user is required to replace it, and until
    they do it is all the account has.
    """

    temporary_password: str = Field(serialization_alias="temporaryPassword")
