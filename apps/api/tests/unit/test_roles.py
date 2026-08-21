"""What each role may do.

Two roles is not least privilege, it is a switch. The distinction an
organisation actually needs is between changing what runs, running it, and
changing who has access -- and the last of those is the one that must not come
free with either of the others.

The routes already ask for permissions rather than roles, so adding a role is
a question of what it holds. That is exactly why it is worth asserting here:
a mistake in this map is invisible everywhere else.
"""

from __future__ import annotations

import itertools

import pytest

from plimsoll_api.security.permissions import (
    ROLE_PERMISSIONS,
    Permission,
    permissions_for,
)


def test_only_an_administrator_may_administer() -> None:
    """The crown jewel: issuing credentials, reading the trail, changing who
    is in the organisation. Nothing else grants it, and no combination of the
    other permissions adds up to it."""
    holders = [
        role for role in ROLE_PERMISSIONS if Permission.ADMIN_SYSTEM in permissions_for(role)
    ]
    assert holders == ["ORG_ADMIN"]


def test_every_role_can_read() -> None:
    """A role that cannot see anything cannot do anything usefully either."""
    for role in ROLE_PERMISSIONS:
        held = permissions_for(role)
        assert Permission.PROJECT_READ in held, role
        assert Permission.TEST_READ in held, role


def test_a_tester_may_run_but_not_change_what_runs() -> None:
    """The CI shape. A pipeline that can edit a threshold can make a failing
    run pass, which is the one thing a gate must not be able to do."""
    held = permissions_for("TESTER")
    assert Permission.TEST_EXECUTE in held
    assert Permission.TEST_WRITE not in held
    assert Permission.SCRIPT_WRITE not in held
    assert Permission.ADMIN_SYSTEM not in held


def test_an_engineer_may_change_what_runs_but_not_who_may_sign_in() -> None:
    held = permissions_for("PERFORMANCE_ENGINEER")
    assert Permission.TEST_WRITE in held
    assert Permission.SCRIPT_WRITE in held
    assert Permission.TEST_EXECUTE in held
    assert Permission.ADMIN_SYSTEM not in held


def test_a_viewer_changes_nothing() -> None:
    held = permissions_for("VIEWER")
    assert not any(
        permission in held
        for permission in (
            Permission.PROJECT_WRITE,
            Permission.SCRIPT_WRITE,
            Permission.TEST_WRITE,
            Permission.TEST_EXECUTE,
            Permission.ADMIN_SYSTEM,
        )
    )


def test_the_roles_are_ordered_by_what_they_hold() -> None:
    """Each role holds everything the one below it does.

    Not a formality: a role that grants something a nominally stronger one
    lacks makes "promote" and "demote" meaningless, and the last-administrator
    guard is built on there being an order.
    """
    ladder = ["VIEWER", "TESTER", "PERFORMANCE_ENGINEER", "ORG_ADMIN"]
    for lower, higher in itertools.pairwise(ladder):
        assert permissions_for(lower) < permissions_for(higher), f"{lower} vs {higher}"


def test_an_unrecognised_role_holds_nothing() -> None:
    """A widened enum, a typo, or a role from a newer version fails closed."""
    assert permissions_for("SUPER_ADMIN") == frozenset()
    assert permissions_for("") == frozenset()


@pytest.mark.parametrize("role", ["VIEWER", "TESTER", "PERFORMANCE_ENGINEER", "ORG_ADMIN"])
def test_every_role_in_the_map_is_offered_by_the_contract(role: str) -> None:
    """A role the API cannot assign is a role nobody has."""
    import typing

    from plimsoll_contracts.users import OrgRole

    assert role in typing.get_args(OrgRole)
