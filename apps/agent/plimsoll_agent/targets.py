"""Invariant 8's second gate, evaluated where the traffic leaves.

The matching rules are the control plane's, restated because the agent cannot
import `plimsoll_api` -- it ships beside a customer's plan and carries nothing
it does not need. The two implementations must stay behaviourally identical;
these rules are small enough that the tests can say so.
"""

from __future__ import annotations

import ipaddress
import re

WHOLE_VARIABLE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
DOMAIN = re.compile(r'<stringProp name="HTTPSampler\.domain">([^<]*)</stringProp>')


class TargetRefused(Exception):
    """A target this generator is not permitted to reach."""


def _matches(host: str, entries: list[str]) -> bool:
    candidate = host.strip().lower()
    if not candidate:
        return False
    for entry in entries:
        rule = entry.strip().lower()
        if "/" in rule:
            try:
                network = ipaddress.ip_network(rule, strict=False)
                if ipaddress.ip_address(candidate) in network:
                    return True
            except ValueError:
                continue
        elif rule.startswith("."):
            suffix = rule.removeprefix(".")
            if candidate == suffix or candidate.endswith(f".{suffix}"):
                return True
        elif candidate == rule:
            return True
    return False


def refuse_disallowed(hosts: list[str], *, allowlist: list[str], variables: dict[str, str]) -> None:
    """Raises rather than reporting: this is the last gate before traffic."""
    rejected: list[str] = []
    for host in hosts:
        match = WHOLE_VARIABLE.match(host.strip())
        resolved = variables.get(match.group(1)) if match else host
        # Unknown is not the same as permitted.
        if resolved is None or "${" in resolved:
            rejected.append(f"{host} (unresolved)")
        elif not _matches(resolved, allowlist):
            rejected.append(resolved if resolved == host else f"{resolved} (from {host})")
    if rejected:
        raise TargetRefused(
            "These targets are not permitted by the organisation's policy: " + ", ".join(rejected)
        )


def hosts_in(plan_xml: str) -> list[str]:
    """Read from the plan this generator is about to execute -- not from what
    something else recorded about it earlier. A remembered list and a plan held
    in the hand are exactly the two things that drift apart unnoticed."""
    return sorted({match.strip() for match in DOMAIN.findall(plan_xml) if match.strip()})
