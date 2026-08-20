"""Invariant 8's second gate, inside the generator.

The first check happened minutes earlier against configuration that could have
changed. This one happens where the traffic actually leaves the machine.
"""

import pytest

from plimsoll_agent.targets import TargetRefused, refuse_disallowed


def test_a_permitted_host_passes() -> None:
    refuse_disallowed(["demo-target"], allowlist=["demo-target"], variables={})


def test_a_host_outside_the_allowlist_is_refused() -> None:
    with pytest.raises(TargetRefused) as raised:
        refuse_disallowed(["evil.example.com"], allowlist=["demo-target"], variables={})
    assert "evil.example.com" in str(raised.value)


def test_a_variable_host_is_resolved_before_checking() -> None:
    refuse_disallowed(
        ["${API_HOST}"], allowlist=["demo-target"], variables={"API_HOST": "demo-target"}
    )


def test_a_variable_resolving_outside_the_allowlist_is_refused() -> None:
    with pytest.raises(TargetRefused):
        refuse_disallowed(
            ["${API_HOST}"],
            allowlist=["demo-target"],
            variables={"API_HOST": "elsewhere.invalid"},
        )


def test_an_unresolvable_variable_is_refused() -> None:
    """Unknown is not the same as permitted."""
    with pytest.raises(TargetRefused):
        refuse_disallowed(["${MISSING}"], allowlist=["demo-target"], variables={})


def test_an_empty_allowlist_permits_nothing() -> None:
    """There is no permit-all state (ADR-0007)."""
    with pytest.raises(TargetRefused):
        refuse_disallowed(["demo-target"], allowlist=[], variables={})


def test_a_suffix_rule_matches_its_subdomains() -> None:
    refuse_disallowed(["api.acme.test"], allowlist=[".acme.test"], variables={})


def test_a_suffix_rule_does_not_match_a_lookalike() -> None:
    with pytest.raises(TargetRefused):
        refuse_disallowed(["api.acme.test.evil.com"], allowlist=[".acme.test"], variables={})
