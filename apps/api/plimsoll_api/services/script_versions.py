"""Pinning is idempotent by construction.

A commit SHA is immutable, so pinning the same (repository, commit, plan path)
twice describes the same thing and returns the row that already exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from plimsoll_api.errors import PlimsollError
from plimsoll_api.git.client import GitAccess, GitError, fetch_plan, resolve_ref
from plimsoll_api.plans.jmx import PlanParseError, PlanSummary, parse_plan
from plimsoll_api.services.verify import plan_checksum
from plimsoll_contracts.errors import ErrorCode


@dataclass(frozen=True)
class PinCandidate:
    sha: str
    checksum: str
    summary: PlanSummary


def summary_as_metadata(summary: PlanSummary) -> dict[str, Any]:
    return {
        "threadGroups": summary.thread_groups,
        "transactionControllers": summary.transaction_controllers,
        "timers": summary.timers,
        "variables": summary.variables,
        "dataFiles": summary.data_files,
        "targets": [
            {"scheme": target.scheme, "host": target.host, "port": target.port}
            for target in summary.targets
        ],
    }


async def resolve_for_pin(access: GitAccess, plan_path: str, ref: str) -> PinCandidate:
    """The network half, run between transactions rather than inside one."""
    try:
        resolution = await resolve_ref(access, ref)
    except GitError as exc:
        raise PlimsollError(
            ErrorCode.REPO_UNREACHABLE, f"Could not resolve {ref!r}: {exc}", {"ref": ref}
        ) from exc

    try:
        async with fetch_plan(access, resolution.sha, plan_path) as root:
            plan_file = root / plan_path
            if not plan_file.is_file():
                raise PlimsollError(
                    ErrorCode.VALIDATION_FAILED,
                    f"No plan at {plan_path} in commit {resolution.sha}.",
                )
            checksum = plan_checksum(plan_file)
            try:
                summary = parse_plan(plan_file.read_text(errors="replace"))
            except PlanParseError as exc:
                raise PlimsollError(
                    ErrorCode.VALIDATION_FAILED, f"The plan does not parse: {exc}"
                ) from exc
    except GitError as exc:
        raise PlimsollError(
            ErrorCode.REPO_UNREACHABLE, f"Could not read the repository: {exc}"
        ) from exc

    return PinCandidate(sha=resolution.sha, checksum=checksum, summary=summary)
