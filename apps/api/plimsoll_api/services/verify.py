"""verify reports; it does not reject.

A reachable repository that does not conform returns every finding at once, so
the tester fixes the set rather than discovering them one request at a time.
REPO_UNREACHABLE is reserved for not reaching Git at all.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from plimsoll_api.errors import PlimsollError
from plimsoll_api.git.client import GitAccess, GitError, fetch_plan, resolve_ref
from plimsoll_api.plans.jmx import PlanParseError, PlanSummary, parse_plan
from plimsoll_api.plans.manifest import Manifest, ManifestError, parse_manifest
from plimsoll_contracts.errors import ErrorCode
from plimsoll_contracts.scripts import (
    Finding,
    FindingSeverity,
    PlanSummaryResponse,
    PlanTargetResponse,
    PluginSummary,
    VerifyReport,
)

MANIFEST_NAME = "plimsoll.yaml"


def _error(code: str, message: str, location: str | None = None) -> Finding:
    return Finding(code=code, severity=FindingSeverity.ERROR, message=message, location=location)


def _warning(code: str, message: str, location: str | None = None) -> Finding:
    return Finding(code=code, severity=FindingSeverity.WARNING, message=message, location=location)


def plan_checksum(plan_file: Path) -> str:
    return hashlib.sha256(plan_file.read_bytes()).hexdigest()


def _find_manifest(root: Path, plan_path: str) -> Path | None:
    """The plan's directory first, then upward to the repository root."""
    directory = (root / plan_path).parent
    while True:
        candidate = directory / MANIFEST_NAME
        if candidate.is_file():
            return candidate
        if directory == root or root not in directory.parents:
            return None
        directory = directory.parent


def _summary_response(summary: PlanSummary) -> PlanSummaryResponse:
    return PlanSummaryResponse(
        thread_groups=summary.thread_groups,
        transaction_controllers=summary.transaction_controllers,
        timers=summary.timers,
        variables=summary.variables,
        data_files=summary.data_files,
        targets=[
            PlanTargetResponse(scheme=target.scheme, host=target.host, port=target.port)
            for target in summary.targets
        ],
    )


def _unresolved_targets(summary: PlanSummary, plan_path: str) -> list[Finding]:
    """A host like ${API_HOST} cannot be checked here.

    verify has no test context and therefore no variable values, so it says so
    rather than guessing. Preflight resolves the variable and checks the real
    host against the allowlist.
    """
    return [
        _warning(
            "TARGET_UNRESOLVED",
            f"Target host {target.host} is a variable; it is checked against the target "
            "policy at preflight, when its value is known.",
            plan_path,
        )
        for target in summary.targets
        if "${" in target.host
    ]


def _manifest_findings(
    manifest: Manifest, plan_path: str, summary: PlanSummary, root: Path
) -> list[Finding]:
    findings: list[Finding] = []
    plan_name = Path(plan_path).name
    entry = next((plan for plan in manifest.plans if Path(plan.path).name == plan_name), None)
    if entry is None:
        return [
            _error(
                "PLAN_NOT_DECLARED",
                f"{plan_path} is not listed under `plans` in the manifest.",
                MANIFEST_NAME,
            )
        ]

    plan_directory = (root / plan_path).parent
    for data in entry.data:
        if not (plan_directory / data.path).is_file():
            findings.append(
                _error(
                    "DATA_FILE_MISSING",
                    f"{data.path} is declared in the manifest but absent at this commit.",
                    MANIFEST_NAME,
                )
            )

    declared = {data.path for data in entry.data}
    for referenced in summary.data_files:
        if referenced not in declared and not (plan_directory / referenced).is_file():
            findings.append(
                _error(
                    "DATA_FILE_UNDECLARED",
                    f"The plan reads {referenced}, which is neither declared nor present.",
                    plan_path,
                )
            )

    undeclared = sorted(set(summary.variables) - set(entry.variables))
    if undeclared:
        findings.append(
            _error(
                "VARIABLE_NOT_DECLARED",
                "The plan references variables the manifest does not declare: "
                + ", ".join(undeclared),
                MANIFEST_NAME,
            )
        )
    return findings


async def run(access: GitAccess, plan_path: str, ref: str) -> VerifyReport:
    try:
        resolution = await resolve_ref(access, ref)
    except GitError as exc:
        raise PlimsollError(
            ErrorCode.REPO_UNREACHABLE,
            f"Could not resolve {ref!r} in the repository: {exc}",
            {"ref": ref},
        ) from exc

    findings: list[Finding] = []
    summary: PlanSummary | None = None
    plugins: list[PluginSummary] = []

    try:
        async with fetch_plan(access, resolution.sha, plan_path) as root:
            plan_file = root / plan_path
            if not plan_file.is_file():
                findings.append(
                    _error("PLAN_MISSING", f"No plan at {plan_path} in commit {resolution.sha}.")
                )
            else:
                try:
                    summary = parse_plan(plan_file.read_text(errors="replace"))
                except PlanParseError as exc:
                    findings.append(_error("PLAN_UNPARSEABLE", str(exc), plan_path))
                else:
                    findings.extend(_unresolved_targets(summary, plan_path))

                manifest_file = _find_manifest(root, plan_path)
                # A manifest is optional: a repository needing nothing beyond
                # stock JMeter is verified from the plan alone.
                if manifest_file is not None:
                    try:
                        manifest = parse_manifest(manifest_file.read_text(errors="replace"))
                    except ManifestError as exc:
                        findings.append(_error("MANIFEST_INVALID", str(exc), MANIFEST_NAME))
                    else:
                        plugins = [
                            PluginSummary(id=p.id, version=p.version, sha256=p.sha256)
                            for p in manifest.plugins
                        ]
                        if summary is not None:
                            findings.extend(_manifest_findings(manifest, plan_path, summary, root))
    except GitError as exc:
        raise PlimsollError(
            ErrorCode.REPO_UNREACHABLE, f"Could not read the repository: {exc}"
        ) from exc

    return VerifyReport(
        # A warning is something to know, not something to fix before running.
        ok=not any(finding.severity is FindingSeverity.ERROR for finding in findings),
        commit_sha=resolution.sha,
        ref=ref,
        findings=findings,
        plan=_summary_response(summary) if summary is not None else None,
        plugins=plugins,
    )
