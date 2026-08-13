"""Every check runs, and every result is reported.

Validation that refuses to tell you the second problem until you fix the first
is what v0.1's definition of done rules out. A check whose input is missing
reports SKIPPED rather than vanishing from the list, so the six codes are always
there and a caller can render the same shape every time.

The Git work happens between transactions: the caller gathers the inputs in one,
closes it, and hands them here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from plimsoll_api.git.client import GitAccess, GitError, fetch_plan, resolve_ref
from plimsoll_api.plans.jmx import PlanParseError, PlanSummary, parse_plan
from plimsoll_api.services.target_policy import matches_allowlist
from plimsoll_contracts.validation import Check, CheckStatus, PreflightReport

# A host written entirely as one variable reference, which is the only form the
# platform can resolve: ${API_HOST} yes, api-${REGION}.example.com no.
WHOLE_VARIABLE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


@dataclass(frozen=True)
class PlanInput:
    repo_name: str
    access: GitAccess
    plan_path: str
    ref: str
    virtual_users: int


@dataclass(frozen=True)
class PreflightInput:
    requested_users: int
    plans: list[PlanInput] = field(default_factory=list)
    allowlist: list[str] = field(default_factory=list)
    # Variable name -> stored value, for every VARIABLE credential in the
    # organisation. Values are used to resolve a target host and nothing else.
    variables: dict[str, str] = field(default_factory=dict)
    # None means the pool is gone or no longer active, which is a capacity
    # failure rather than a reason to refuse the whole report.
    free_capacity: int | None = None


def _passed(code: str, detail: str) -> Check:
    return Check(code=code, status=CheckStatus.PASS, detail=detail)


def _failed(code: str, detail: str) -> Check:
    return Check(code=code, status=CheckStatus.FAIL, detail=detail)


def _skipped(code: str, detail: str) -> Check:
    return Check(code=code, status=CheckStatus.SKIPPED, detail=detail)


def _structure_check(inputs: PreflightInput) -> Check:
    declared = sum(plan.virtual_users for plan in inputs.plans)
    if not inputs.plans:
        return _failed("TEST_STRUCTURE", "The test has no plan to execute.")
    if declared != inputs.requested_users:
        return _failed(
            "TEST_STRUCTURE",
            f"The plans allocate {declared} virtual users; the workload asks for "
            f"{inputs.requested_users}.",
        )
    return _passed("TEST_STRUCTURE", f"{len(inputs.plans)} plan(s), {declared} virtual users.")


def _variables_check(
    summaries: list[PlanSummary], inputs: PreflightInput, *, blocked: bool
) -> Check:
    """Existence only.

    The value resolves at run start and is injected in memory, so knowing a
    variable has one is the whole of what can be checked here.
    """
    if blocked:
        return _skipped("VARIABLES_PRESENT", "Not attempted: no plan was read.")

    referenced = sorted({name for summary in summaries for name in summary.variables})
    missing = [name for name in referenced if name not in inputs.variables]
    if missing:
        return _failed(
            "VARIABLES_PRESENT",
            "The plan references variables with no stored value: " + ", ".join(missing),
        )
    return _passed(
        "VARIABLES_PRESENT",
        f"{len(referenced)} variable(s) resolved." if referenced else "No variables referenced.",
    )


def _resolve_host(host: str, variables: dict[str, str]) -> str | None:
    """None means it stayed a variable, which cannot be checked and so fails."""
    match = WHOLE_VARIABLE.match(host.strip())
    if match is None:
        return host if "${" not in host else None
    return variables.get(match.group(1))


def _targets_check(summaries: list[PlanSummary], inputs: PreflightInput, *, blocked: bool) -> Check:
    if blocked:
        return _skipped("TARGET_ALLOWED", "Not attempted: no plan was read.")

    hosts = sorted({target.host for summary in summaries for target in summary.targets})
    if not hosts:
        return _failed("TARGET_ALLOWED", "The plan names no target to check.")

    rejected: list[str] = []
    unresolved: list[str] = []
    allowed: list[str] = []
    for host in hosts:
        resolved = _resolve_host(host, inputs.variables)
        if resolved is None:
            unresolved.append(host)
        elif matches_allowlist(resolved, inputs.allowlist):
            allowed.append(resolved)
        else:
            rejected.append(resolved if resolved == host else f"{resolved} (from {host})")

    if unresolved or rejected:
        # An absent policy is an empty allowlist, which permits nothing: the
        # check rejects, and there is no permit-all state.
        reasons = []
        if rejected:
            reasons.append("outside the target policy allowlist: " + ", ".join(rejected))
        if unresolved:
            reasons.append("unresolved: " + ", ".join(unresolved))
        return _failed("TARGET_ALLOWED", "; ".join(reasons))
    # Deduplicated: a literal host and a variable that resolves to it are one
    # target, and reporting it twice reads as a mistake.
    return _passed(
        "TARGET_ALLOWED", "Permitted by the target policy: " + ", ".join(sorted(set(allowed)))
    )


def _capacity_check(inputs: PreflightInput) -> Check:
    if inputs.free_capacity is None:
        return _failed("CAPACITY", "The test's generator pool no longer exists or is not active.")
    if inputs.requested_users <= inputs.free_capacity:
        return _passed("CAPACITY", f"{inputs.requested_users} of {inputs.free_capacity} available.")
    return _failed(
        "CAPACITY",
        f"The test asks for {inputs.requested_users} virtual users; the pool has "
        f"{inputs.free_capacity} available.",
    )


async def run(inputs: PreflightInput) -> PreflightReport:
    checks: list[Check] = [_structure_check(inputs)]

    # Every ref resolves. Failures accumulate; the loop does not stop.
    resolved: dict[str, str] = {}
    unresolved: list[str] = []
    for plan in inputs.plans:
        try:
            resolution = await resolve_ref(plan.access, plan.ref)
        except GitError as exc:
            unresolved.append(f"{plan.repo_name} at {plan.ref}: {exc}")
        else:
            resolved[plan.repo_name] = resolution.sha
    checks.append(
        _failed("SCRIPT_REF", "; ".join(unresolved))
        if unresolved
        else _passed(
            "SCRIPT_REF",
            ", ".join(f"{name}@{sha[:8]}" for name, sha in resolved.items())
            or "No ref to resolve.",
        )
    )

    # The plan-derived checks share one fetch each.
    summaries: list[PlanSummary] = []
    parse_failures: list[str] = []
    for plan in inputs.plans:
        sha = resolved.get(plan.repo_name)
        if sha is None:
            continue
        try:
            async with fetch_plan(plan.access, sha, plan.plan_path) as root:
                summaries.append(parse_plan((root / plan.plan_path).read_text(errors="replace")))
        except (GitError, OSError, PlanParseError) as exc:
            parse_failures.append(f"{plan.repo_name}: {exc}")

    if unresolved:
        checks.append(_skipped("PLAN_PARSES", "Not attempted: a ref did not resolve."))
    elif parse_failures:
        checks.append(_failed("PLAN_PARSES", "; ".join(parse_failures)))
    else:
        checks.append(_passed("PLAN_PARSES", f"{len(summaries)} plan(s) parsed."))

    blocked = bool(unresolved or parse_failures)
    checks.append(_variables_check(summaries, inputs, blocked=blocked))
    checks.append(_targets_check(summaries, inputs, blocked=blocked))
    checks.append(_capacity_check(inputs))

    return PreflightReport(
        ok=all(check.status is CheckStatus.PASS for check in checks), checks=checks
    )
