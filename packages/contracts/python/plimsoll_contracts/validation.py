from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class CheckStatus(StrEnum):
    PASS = "PASS"  # noqa: S105 - a check outcome, not a credential
    FAIL = "FAIL"
    # The check could not run because something it depends on failed. Reported
    # rather than omitted, so the caller sees the whole picture at once.
    SKIPPED = "SKIPPED"


class Check(BaseModel):
    code: str
    status: CheckStatus
    detail: str


class PreflightReport(BaseModel):
    ok: bool
    checks: list[Check]
