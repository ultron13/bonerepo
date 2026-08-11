from __future__ import annotations

from typing import Protocol

from fastapi import APIRouter, FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse

from plimsoll_api.config import get_settings

router = APIRouter()

_CHECKS_ATTR = "plimsoll_readiness_checks"


class DependencyCheck(Protocol):
    name: str

    async def check(self) -> bool: ...


def set_readiness_checks(app: FastAPI, checks: list[DependencyCheck]) -> None:
    setattr(app.state, _CHECKS_ATTR, checks)


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    """Liveness only. It must never touch a dependency: a failing database is a
    readiness problem, and restarting the process would not fix it."""
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
async def readyz(request: Request) -> JSONResponse:
    checks: list[DependencyCheck] = getattr(request.app.state, _CHECKS_ATTR, [])
    results = {check.name: await check.check() for check in checks}
    healthy = all(results.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={"status": "ready" if healthy else "not-ready", "checks": results},
    )


@router.get("/api/v1/version")
async def version() -> dict[str, str]:
    settings = get_settings()
    return {"version": settings.version, "gitSha": settings.git_sha}
