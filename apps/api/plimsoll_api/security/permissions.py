"""What a principal may do, checked server-side on every route.

The names are the ones the data model already documents. v0.1 issues two roles;
the v0.3 matrix changes this map and the way a principal's roles are resolved,
not the endpoints that consume it.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from plimsoll_api.dependencies import CurrentPrincipal
from plimsoll_api.errors import PlimsollError
from plimsoll_api.security.tokens import AccessClaims
from plimsoll_contracts.errors import ErrorCode


class Permission(StrEnum):
    PROJECT_READ = "project.read"
    PROJECT_WRITE = "project.write"
    SCRIPT_READ = "script.read"
    SCRIPT_WRITE = "script.write"
    TEST_READ = "test.read"
    TEST_WRITE = "test.write"
    TEST_EXECUTE = "test.execute"
    ADMIN_SYSTEM = "admin.system"


READ_PERMISSIONS = frozenset(
    {Permission.PROJECT_READ, Permission.SCRIPT_READ, Permission.TEST_READ}
)

ROLE_PERMISSIONS: dict[str, frozenset[Permission]] = {
    "ORG_ADMIN": frozenset(Permission),
    "VIEWER": READ_PERMISSIONS,
}


def permissions_for(role: str) -> frozenset[Permission]:
    """An unrecognised role holds nothing, so a widened enum fails closed."""
    return ROLE_PERMISSIONS.get(role, frozenset())


def held_by(principal: AccessClaims) -> frozenset[Permission]:
    """What this principal may do.

    A key's scopes replace its role rather than adding to it. Reading both
    would make a scoped key as powerful as the person who created it, which is
    the whole thing scopes exist to prevent.
    """
    if principal.scopes is not None:
        return frozenset(
            permission for permission in Permission if permission.value in principal.scopes
        )
    return permissions_for(principal.role)


def requires(permission: Permission) -> Callable[..., None]:
    def guard(principal: CurrentPrincipal) -> None:
        if permission not in held_by(principal):
            raise PlimsollError(
                ErrorCode.PERMISSION_DENIED,
                f"This action requires the {permission} permission.",
            )

    return guard
