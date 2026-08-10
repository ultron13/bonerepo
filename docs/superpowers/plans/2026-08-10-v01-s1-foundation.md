# Plimsoll v0.1 Slice 1 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `make dev` brings up the stack on a clean machine, an operator can log in, and PostgreSQL row-level security is proven to isolate organisations by a test that fails when the boundary is removed.

**Architecture:** A modular monolith. One FastAPI application layered `router → service → repository → model`, talking to PostgreSQL (with the TimescaleDB extension), Redis, and MinIO. Tenancy is enforced by PostgreSQL policies keyed on a transaction-local setting, not by application filters. Every command runs inside Docker containers.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (async, asyncpg), Alembic, Pydantic v2, `uv`, `ruff`, `mypy`, pytest, PostgreSQL 16 + TimescaleDB, Redis, MinIO, Docker Compose, pnpm, `openapi-typescript`.

## Global Constraints

Every task's requirements implicitly include this section.

- **Python 3.12+.** Containers pin `python:3.12-slim`. Do not rely on 3.13+ syntax.
- **SQLAlchemy 2.0 style** — `Mapped`/`mapped_column`, no legacy `Query`.
- **Pydantic v2** — `model_config`, not v1 `class Config`.
- **Format with `ruff format`, lint with `ruff`, type-check with `mypy`.**
- **Timestamps are `TIMESTAMPTZ`, stored UTC, ISO-8601 at the boundary.**
- **Database access goes through a repository. API responses are DTOs.** Never serialise a SQLAlchemy model to a client.
- **No user-facing string literals inside business logic.**
- **Only Docker and make are prerequisites.** If a target needs another host tool, that is a bug.
- **Naming:** `plimsoll` / `plim` only. Never OpenText, LoadRunner, LRE, or VuGen.
- **Environment variables are prefixed `PLIMSOLL_`.**
- **Commits are signed off** (`git commit -s`) — CI rejects unsigned commits.
- **Every task ends with a passing test run and a commit.**

## File Structure

```
Makefile                                    make targets, all container-based
.env.example                                every PLIMSOLL_ var, no secrets
pyproject.toml                              uv workspace root, ruff + mypy config
uv.lock                                     hash-pinned, committed
package.json / pnpm-workspace.yaml          TS workspace root

packages/contracts/python/plimsoll_contracts/
  errors.py            ErrorCode enum, ErrorDetail, ErrorEnvelope
  pagination.py        Page[T], cursor encode/decode
  auth.py              LoginRequest, TokenPair, MeResponse
packages/contracts/typescript/              generated.ts (produced, committed)

apps/api/plimsoll_api/
  main.py              app factory, router registration
  config.py            Settings (pydantic-settings)
  logging.py           JSON formatter carrying request_id
  middleware.py        request-ID middleware
  errors.py            exception handlers -> ErrorEnvelope
  db/session.py        engine, request-scoped session with org setting
  db/models/*.py       SQLAlchemy models, one module per concern
  security/passwords.py   Argon2id
  security/tokens.py      JWT access + refresh rotation
  repositories/*.py    data access
  services/*.py        business logic
  routers/health.py    /healthz /readyz /api/v1/version
  routers/auth.py      /api/v1/auth/*
  migrations/          alembic env + versions
  seed.py              demo org, users, project, target policy
apps/api/tests/unit/                        no database
apps/api/tests/integration/                 requires make dev

images/demo-target/app.py                   latency-shaped system under test
infrastructure/docker/
  docker-compose.yml
  api.Dockerfile
  demo-target.Dockerfile
  postgres-init/01-roles.sql                creates plimsoll_owner, plimsoll_app
```

---

### Task 1: Monorepo skeleton and Python tooling

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.dockerignore`, `Makefile`
- Create: `apps/api/pyproject.toml`, `apps/api/plimsoll_api/__init__.py`
- Create: `packages/contracts/python/pyproject.toml`, `packages/contracts/python/plimsoll_contracts/__init__.py`
- Create: `apps/worker/README.md`, `apps/agent/README.md`, `apps/web/README.md`, `packages/executor-sdk/README.md`, `images/generator/README.md`
- Test: `apps/api/tests/unit/test_smoke.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a `uv` workspace where `uv run pytest` works from the repository root; package names `plimsoll_api` and `plimsoll_contracts`.

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/unit/test_smoke.py`:

```python
def test_packages_are_importable() -> None:
    import plimsoll_api
    import plimsoll_contracts

    assert plimsoll_api.__name__ == "plimsoll_api"
    assert plimsoll_contracts.__name__ == "plimsoll_contracts"
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/unit/test_smoke.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'plimsoll_api'`.

- [ ] **Step 3: Create the workspace root `pyproject.toml`**

```toml
[project]
name = "plimsoll"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = []

[tool.uv.workspace]
members = ["apps/api", "packages/contracts/python"]

[tool.uv.sources]
plimsoll-api = { workspace = true }
plimsoll-contracts = { workspace = true }

[dependency-groups]
dev = ["pytest>=8.2", "pytest-asyncio>=0.23", "ruff>=0.5", "mypy>=1.10", "httpx>=0.27"]

[tool.ruff]
line-length = 100
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "ASYNC", "S", "RUF"]
ignore = ["S101"]

[tool.ruff.lint.per-file-ignores]
"**/tests/**" = ["S105", "S106"]

[tool.mypy]
python_version = "3.12"
strict = true
warn_unreachable = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["apps/api/tests"]
markers = ["integration: requires the make dev stack"]
```

- [ ] **Step 4: Create the member packages**

`apps/api/pyproject.toml`:

```toml
[project]
name = "plimsoll-api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["plimsoll-contracts"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["plimsoll_api"]
```

`packages/contracts/python/pyproject.toml`:

```toml
[project]
name = "plimsoll-contracts"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["pydantic>=2.7"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["plimsoll_contracts"]
```

Create empty `apps/api/plimsoll_api/__init__.py` and `packages/contracts/python/plimsoll_contracts/__init__.py`.

- [ ] **Step 5: Create `.gitignore` and `.dockerignore`**

`.gitignore`:

```
__pycache__/
*.py[cod]
.venv/
.env
.pytest_cache/
.mypy_cache/
.ruff_cache/
node_modules/
.next/
dist/
```

`.dockerignore`:

```
.git
.venv
node_modules
**/__pycache__
**/.pytest_cache
**/.mypy_cache
```

- [ ] **Step 6: Create the Makefile**

`make dev` is added in Task 7; these targets work now.

```makefile
.PHONY: test lint typecheck format

UV := uv run

test:
	$(UV) pytest apps/api/tests/unit -v

lint:
	$(UV) ruff check .
	$(UV) ruff format --check .

typecheck:
	$(UV) mypy apps packages

format:
	$(UV) ruff format .
```

- [ ] **Step 7: Create placeholder READMEs for later slices**

Each file states which slice fills it. `apps/worker/README.md`:

```markdown
# plimsoll worker

Not yet implemented. The execution orchestrator and metrics ingestion land in
slice 3 (Execution) and slice 4 (Metrics) of v0.1.

See `docs/superpowers/specs/2026-08-10-v01-s1-foundation-design.md`.
```

Repeat with the right slice for `apps/agent/README.md` (slice 3), `apps/web/README.md` (slice 5), `packages/executor-sdk/README.md` (slice 3), `images/generator/README.md` (slice 3).

- [ ] **Step 8: Run the tests and make sure they pass**

Run: `uv sync && make test && make lint && make typecheck`
Expected: the smoke test passes; lint and typecheck report no errors.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -s -m "feat: monorepo skeleton with uv workspace and tooling"
```

---

### Task 2: Contracts — error codes, envelope, and cursor pagination

**Files:**
- Create: `packages/contracts/python/plimsoll_contracts/errors.py`
- Create: `packages/contracts/python/plimsoll_contracts/pagination.py`
- Test: `apps/api/tests/unit/test_errors.py`, `apps/api/tests/unit/test_pagination.py`

**Interfaces:**
- Consumes: Task 1's package layout.
- Produces: `ErrorCode` (str enum), `ErrorEnvelope.of(code, message, request_id, details) -> ErrorEnvelope`, `Page[T]` with fields `items: list[T]` and `next_cursor: str | None`, `encode_cursor(dict) -> str`, `decode_cursor(str) -> dict`, `DEFAULT_PAGE_LIMIT = 50`, `MAX_PAGE_LIMIT = 200`.

- [ ] **Step 1: Write the failing tests**

`apps/api/tests/unit/test_errors.py`:

```python
import pytest

from plimsoll_contracts.errors import ErrorCode, ErrorEnvelope


def test_catalogue_matches_the_api_contract() -> None:
    assert {c.value for c in ErrorCode} == {
        "VALIDATION_FAILED",
        "UNAUTHENTICATED",
        "PERMISSION_DENIED",
        "NOT_FOUND",
        "CONFLICT",
        "IDEMPOTENCY_KEY_REUSED",
        "TEST_NOT_RUNNABLE",
        "TARGET_NOT_ALLOWED",
        "INSUFFICIENT_CAPACITY",
        "REPO_UNREACHABLE",
        "RATE_LIMITED",
        "INTERNAL",
    }


def test_envelope_serialises_to_the_documented_shape() -> None:
    envelope = ErrorEnvelope.of(
        ErrorCode.NOT_FOUND, "No such project.", request_id="req-1", details={"id": "x"}
    )
    assert envelope.model_dump(mode="json", exclude_none=True) == {
        "error": {
            "code": "NOT_FOUND",
            "message": "No such project.",
            "details": {"id": "x"},
            "requestId": "req-1",
        }
    }


def test_details_omitted_when_absent() -> None:
    envelope = ErrorEnvelope.of(ErrorCode.INTERNAL, "Boom.", request_id="req-2")
    dumped = envelope.model_dump(mode="json", exclude_none=True)
    assert "details" not in dumped["error"]
```

`apps/api/tests/unit/test_pagination.py`:

```python
import pytest

from plimsoll_contracts.pagination import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    decode_cursor,
    encode_cursor,
)


def test_cursor_round_trips() -> None:
    payload = {"created_at": "2026-08-10T10:00:00Z", "id": "abc"}
    assert decode_cursor(encode_cursor(payload)) == payload


def test_cursor_is_opaque() -> None:
    encoded = encode_cursor({"id": "abc"})
    assert "abc" not in encoded


def test_malformed_cursor_is_rejected() -> None:
    with pytest.raises(ValueError):
        decode_cursor("not-a-cursor")


def test_limits() -> None:
    assert DEFAULT_PAGE_LIMIT == 50
    assert MAX_PAGE_LIMIT == 200
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run pytest apps/api/tests/unit/test_errors.py apps/api/tests/unit/test_pagination.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'plimsoll_contracts.errors'`.

- [ ] **Step 3: Implement `errors.py`**

```python
"""The error contract. Codes are append-only and frozen under the v1 API."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(StrEnum):
    VALIDATION_FAILED = "VALIDATION_FAILED"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    TEST_NOT_RUNNABLE = "TEST_NOT_RUNNABLE"
    TARGET_NOT_ALLOWED = "TARGET_NOT_ALLOWED"
    INSUFFICIENT_CAPACITY = "INSUFFICIENT_CAPACITY"
    REPO_UNREACHABLE = "REPO_UNREACHABLE"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL = "INTERNAL"


HTTP_STATUS_FOR_CODE: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_FAILED: 422,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.PERMISSION_DENIED: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.IDEMPOTENCY_KEY_REUSED: 409,
    ErrorCode.TEST_NOT_RUNNABLE: 422,
    ErrorCode.TARGET_NOT_ALLOWED: 422,
    ErrorCode.INSUFFICIENT_CAPACITY: 422,
    ErrorCode.REPO_UNREACHABLE: 422,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.INTERNAL: 500,
}


class ErrorBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: ErrorCode
    message: str
    details: dict[str, Any] | None = None
    request_id: str = Field(serialization_alias="requestId")


class ErrorEnvelope(BaseModel):
    error: ErrorBody

    @classmethod
    def of(
        cls,
        code: ErrorCode,
        message: str,
        request_id: str,
        details: dict[str, Any] | None = None,
    ) -> ErrorEnvelope:
        return cls(
            error=ErrorBody(
                code=code, message=message, details=details, request_id=request_id
            )
        )
```

Note: `model_dump(mode="json")` must emit `requestId`. Add `by_alias=True` where dumping, or set `model_config = ConfigDict(serialize_by_alias=True)` on `ErrorBody`. Use the latter so callers cannot forget.

- [ ] **Step 4: Implement `pagination.py`**

```python
"""Opaque cursor pagination. Offsets skip or duplicate rows under concurrent
inserts, which on a busy control plane is always."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200

T = TypeVar("T")


def encode_cursor(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict[str, Any]:
    padding = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + padding)
        decoded = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed cursor.") from exc
    if not isinstance(decoded, dict):
        raise ValueError("Malformed cursor.")
    return decoded


class Page(BaseModel, Generic[T]):
    model_config = ConfigDict(serialize_by_alias=True)

    items: list[T]
    next_cursor: str | None = Field(default=None, serialization_alias="nextCursor")
```

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `uv run pytest apps/api/tests/unit -v`
Expected: PASS — all seven tests.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -s -m "feat(contracts): error catalogue, envelope, and cursor pagination"
```

---

### Task 3: Configuration and structured logging

**Files:**
- Create: `apps/api/plimsoll_api/config.py`, `apps/api/plimsoll_api/logging.py`
- Modify: `apps/api/pyproject.toml` (add `pydantic-settings`)
- Test: `apps/api/tests/unit/test_config.py`, `apps/api/tests/unit/test_logging.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: `Settings` with fields `database_url: str`, `redis_url: str`, `s3_endpoint: str`, `jwt_secret: str`, `access_token_ttl_seconds: int = 900`, `refresh_token_ttl_seconds: int = 1209600`, `environment: str = "development"`; `get_settings() -> Settings` (cached); `configure_logging(level: str) -> None`; `request_id_var: ContextVar[str]`.

- [ ] **Step 1: Write the failing tests**

`apps/api/tests/unit/test_config.py`:

```python
import pytest

from plimsoll_api.config import Settings


def test_reads_prefixed_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLIMSOLL_DATABASE_URL", "postgresql+asyncpg://u@h/db")
    monkeypatch.setenv("PLIMSOLL_REDIS_URL", "redis://h:6379/0")
    monkeypatch.setenv("PLIMSOLL_S3_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("PLIMSOLL_JWT_SECRET", "secret-value")

    settings = Settings()

    assert settings.database_url == "postgresql+asyncpg://u@h/db"
    assert settings.access_token_ttl_seconds == 900
    assert settings.refresh_token_ttl_seconds == 1209600


def test_missing_required_setting_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("DATABASE_URL", "REDIS_URL", "S3_ENDPOINT", "JWT_SECRET"):
        monkeypatch.delenv(f"PLIMSOLL_{key}", raising=False)
    with pytest.raises(ValueError):
        Settings()
```

`apps/api/tests/unit/test_logging.py`:

```python
import json
import logging

from plimsoll_api.logging import configure_logging, request_id_var


def test_log_records_are_json_carrying_the_request_id(
    capsys: object, caplog: object
) -> None:
    configure_logging("INFO")
    request_id_var.set("req-abc")
    logger = logging.getLogger("plimsoll.test")
    stream = logger.handlers[0].stream if logger.handlers else None  # noqa: F841

    import io

    buffer = io.StringIO()
    handler = logging.getLogger().handlers[0]
    original = handler.stream
    handler.stream = buffer
    try:
        logging.getLogger("plimsoll.test").info("hello")
    finally:
        handler.stream = original

    record = json.loads(buffer.getvalue().strip())
    assert record["message"] == "hello"
    assert record["requestId"] == "req-abc"
    assert record["level"] == "INFO"
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run pytest apps/api/tests/unit/test_config.py apps/api/tests/unit/test_logging.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'plimsoll_api.config'`.

- [ ] **Step 3: Add the dependency**

Add `pydantic-settings>=2.3` to `apps/api/pyproject.toml` dependencies, then `uv sync`.

- [ ] **Step 4: Implement `config.py`**

```python
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLIMSOLL_", extra="ignore")

    database_url: str
    redis_url: str
    s3_endpoint: str
    jwt_secret: str

    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 1_209_600
    environment: str = "development"
    log_level: str = "INFO"
    version: str = "0.1.0"
    git_sha: str = "unknown"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 5: Implement `logging.py`**

```python
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "requestId": request_id_var.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
```

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `uv run pytest apps/api/tests/unit -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -s -m "feat(api): settings and structured JSON logging"
```

---

### Task 4: Application factory, request IDs, and error handlers

**Files:**
- Create: `apps/api/plimsoll_api/middleware.py`, `apps/api/plimsoll_api/errors.py`, `apps/api/plimsoll_api/main.py`
- Modify: `apps/api/pyproject.toml` (add `fastapi`, `uvicorn`)
- Test: `apps/api/tests/unit/test_error_handling.py`

**Interfaces:**
- Consumes: `ErrorCode`, `ErrorEnvelope`, `HTTP_STATUS_FOR_CODE` (Task 2); `get_settings`, `configure_logging`, `request_id_var` (Task 3).
- Produces: `create_app() -> FastAPI`; `PlimsollError(code: ErrorCode, message: str, details: dict | None = None)` exception raisable from any layer.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/unit/test_error_handling.py`:

```python
from fastapi.testclient import TestClient

from plimsoll_api.main import create_app


def client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def test_unknown_route_returns_the_error_envelope() -> None:
    response = client().get("/api/v1/nope")
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["requestId"]


def test_request_id_is_echoed_in_the_header() -> None:
    response = client().get("/api/v1/nope", headers={"X-Request-ID": "req-supplied"})
    assert response.headers["x-request-id"] == "req-supplied"
    assert response.json()["error"]["requestId"] == "req-supplied"


def test_request_id_is_generated_when_absent() -> None:
    response = client().get("/api/v1/nope")
    assert response.headers["x-request-id"]


def test_validation_failures_report_every_problem_at_once() -> None:
    app = create_app()

    from pydantic import BaseModel

    class Body(BaseModel):
        a: int
        b: int

    @app.post("/api/v1/_test-validation")
    async def _endpoint(body: Body) -> dict[str, int]:
        return {"ok": 1}

    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/_test-validation", json={"a": "x", "b": "y"}
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_FAILED"
    assert len(body["error"]["details"]["fields"]) == 2
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/unit/test_error_handling.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'plimsoll_api.main'`.

- [ ] **Step 3: Add dependencies**

Add `fastapi>=0.111` and `uvicorn[standard]>=0.30` to `apps/api/pyproject.toml`, then `uv sync`.

- [ ] **Step 4: Implement `middleware.py`**

```python
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

from plimsoll_api.logging import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"


async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get(REQUEST_ID_HEADER) or f"req-{uuid.uuid4().hex[:12]}"
    token = request_id_var.set(request_id)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response
```

- [ ] **Step 5: Implement `errors.py`**

```python
from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from plimsoll_api.logging import request_id_var
from plimsoll_contracts.errors import HTTP_STATUS_FOR_CODE, ErrorCode, ErrorEnvelope

logger = logging.getLogger(__name__)

_STATUS_TO_CODE = {
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.PERMISSION_DENIED,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    429: ErrorCode.RATE_LIMITED,
}


class PlimsollError(Exception):
    def __init__(
        self, code: ErrorCode, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def _envelope(code: ErrorCode, message: str, details: dict[str, Any] | None) -> JSONResponse:
    envelope = ErrorEnvelope.of(code, message, request_id_var.get(), details)
    return JSONResponse(
        status_code=HTTP_STATUS_FOR_CODE[code],
        content=envelope.model_dump(mode="json", exclude_none=True),
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(PlimsollError)
    async def _plimsoll(_: Request, exc: PlimsollError) -> JSONResponse:
        return _envelope(exc.code, exc.message, exc.details)

    @app.exception_handler(HTTPException)
    async def _http(_: Request, exc: HTTPException) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL)
        return _envelope(code, str(exc.detail), None)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"field": ".".join(str(p) for p in error["loc"][1:]), "problem": error["msg"]}
            for error in exc.errors()
        ]
        return _envelope(
            ErrorCode.VALIDATION_FAILED,
            "The request could not be accepted. See details for every problem found.",
            {"fields": fields},
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception")
        return _envelope(
            ErrorCode.INTERNAL,
            "An unexpected error occurred. Quote the request ID when reporting it.",
            None,
        )
```

- [ ] **Step 6: Implement `main.py`**

```python
from __future__ import annotations

from fastapi import FastAPI

from plimsoll_api.config import get_settings
from plimsoll_api.errors import register_error_handlers
from plimsoll_api.logging import configure_logging
from plimsoll_api.middleware import request_id_middleware


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Plimsoll",
        version=settings.version,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
    )
    app.middleware("http")(request_id_middleware)
    register_error_handlers(app)
    return app


app = create_app()
```

Tests must not require real infrastructure, so add a `conftest.py` at `apps/api/tests/conftest.py` setting the required environment before import:

```python
import os

os.environ.setdefault("PLIMSOLL_DATABASE_URL", "postgresql+asyncpg://u:p@localhost/plimsoll")
os.environ.setdefault("PLIMSOLL_REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("PLIMSOLL_S3_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("PLIMSOLL_JWT_SECRET", "test-secret-not-for-production")
```

- [ ] **Step 7: Run the tests and make sure they pass**

Run: `uv run pytest apps/api/tests/unit -v`
Expected: PASS — four error-handling tests plus the earlier ones.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -s -m "feat(api): app factory, request IDs, and the error envelope"
```

---

### Task 5: Health, readiness, and version endpoints

**Files:**
- Create: `apps/api/plimsoll_api/routers/__init__.py`, `apps/api/plimsoll_api/routers/health.py`
- Modify: `apps/api/plimsoll_api/main.py` (register the router)
- Test: `apps/api/tests/unit/test_health.py`

**Interfaces:**
- Consumes: `create_app` (Task 4), `get_settings` (Task 3).
- Produces: `router` at module scope in `routers/health.py`; a `DependencyCheck` protocol with `name: str` and `async def check() -> bool`; `set_readiness_checks(app, checks)` used by Task 7 to inject real ones.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/unit/test_health.py`:

```python
from fastapi.testclient import TestClient

from plimsoll_api.main import create_app
from plimsoll_api.routers.health import DependencyCheck, set_readiness_checks


class StubCheck:
    def __init__(self, name: str, healthy: bool) -> None:
        self.name = name
        self._healthy = healthy

    async def check(self) -> bool:
        return self._healthy


def test_healthz_is_always_ok_and_checks_nothing() -> None:
    app = create_app()
    set_readiness_checks(app, [StubCheck("postgres", False)])
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_reports_ok_when_every_dependency_is_up() -> None:
    app = create_app()
    set_readiness_checks(app, [StubCheck("postgres", True), StubCheck("redis", True)])
    response = TestClient(app).get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"postgres": True, "redis": True}}


def test_readyz_is_503_when_a_dependency_is_down() -> None:
    app = create_app()
    set_readiness_checks(app, [StubCheck("postgres", True), StubCheck("redis", False)])
    response = TestClient(app).get("/readyz")
    assert response.status_code == 503
    assert response.json()["checks"] == {"postgres": True, "redis": False}


def test_version_reports_build_metadata() -> None:
    response = TestClient(create_app()).get("/api/v1/version")
    assert response.status_code == 200
    body = response.json()
    assert body["version"]
    assert "gitSha" in body
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/unit/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'plimsoll_api.routers'`.

- [ ] **Step 3: Implement `routers/health.py`**

```python
from __future__ import annotations

from typing import Protocol

from fastapi import APIRouter, FastAPI
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
    return {"status": "ok"}


@router.get("/readyz", include_in_schema=False)
async def readyz(request_app: FastAPI) -> JSONResponse:  # replaced below
    raise NotImplementedError
```

FastAPI cannot inject the app that way. Use the `Request` object instead — replace the `readyz` handler with:

```python
from starlette.requests import Request


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
```

- [ ] **Step 4: Register the router in `main.py`**

Add after `register_error_handlers(app)`:

```python
    from plimsoll_api.routers import health

    app.include_router(health.router)
```

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `uv run pytest apps/api/tests/unit -v`
Expected: PASS — four health tests.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -s -m "feat(api): health, readiness, and version endpoints"
```

---

### Task 6: The demo target service

**Files:**
- Create: `images/demo-target/app.py`, `images/demo-target/pyproject.toml`, `infrastructure/docker/demo-target.Dockerfile`
- Test: `apps/api/tests/unit/test_demo_target.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a FastAPI app at `images/demo-target/app.py` exposing `GET /login`, `GET /browse`, `GET /cart`, `GET /checkout`, and `GET /healthz`; module-level `LATENCY_PROFILE_MS: dict[str, tuple[int, int]]` and `ERROR_RATE: float`.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/unit/test_demo_target.py`:

```python
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "images" / "demo-target"))

from app import ERROR_RATE, LATENCY_PROFILE_MS, app  # noqa: E402


def test_every_documented_endpoint_exists() -> None:
    client = TestClient(app)
    for path in ("/login", "/browse", "/cart", "/checkout"):
        assert client.get(path).status_code in (200, 500)


def test_healthz_never_fails() -> None:
    client = TestClient(app)
    for _ in range(20):
        assert client.get("/healthz").status_code == 200


def test_latency_profile_covers_every_endpoint_and_is_ordered() -> None:
    assert set(LATENCY_PROFILE_MS) == {"login", "browse", "cart", "checkout"}
    for low, high in LATENCY_PROFILE_MS.values():
        assert 0 < low < high


def test_error_rate_is_low_but_non_zero() -> None:
    assert 0 < ERROR_RATE < 0.05
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/unit/test_demo_target.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app'`.

- [ ] **Step 3: Implement `images/demo-target/app.py`**

```python
"""A deliberately latency-shaped system under test.

A static response would produce a degenerate distribution and make the merged
percentile demonstration in slice 4 meaningless, so each endpoint has its own
latency band and a small error rate.
"""

from __future__ import annotations

import asyncio
import random

from fastapi import FastAPI, HTTPException

LATENCY_PROFILE_MS: dict[str, tuple[int, int]] = {
    "login": (40, 180),
    "browse": (25, 120),
    "cart": (60, 260),
    "checkout": (120, 900),
}

ERROR_RATE = 0.01

app = FastAPI(title="Plimsoll demo target")


async def _serve(name: str) -> dict[str, str]:
    low, high = LATENCY_PROFILE_MS[name]
    await asyncio.sleep(random.uniform(low, high) / 1000)  # noqa: S311
    if random.random() < ERROR_RATE:  # noqa: S311
        raise HTTPException(status_code=500, detail="Simulated downstream failure.")
    return {"transaction": name, "status": "ok"}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/login")
async def login() -> dict[str, str]:
    return await _serve("login")


@app.get("/browse")
async def browse() -> dict[str, str]:
    return await _serve("browse")


@app.get("/cart")
async def cart() -> dict[str, str]:
    return await _serve("cart")


@app.get("/checkout")
async def checkout() -> dict[str, str]:
    return await _serve("checkout")
```

- [ ] **Step 4: Write the Dockerfile**

`infrastructure/docker/demo-target.Dockerfile`:

```dockerfile
FROM python:3.12-slim
WORKDIR /srv
RUN pip install --no-cache-dir "fastapi>=0.111" "uvicorn[standard]>=0.30"
COPY images/demo-target/app.py /srv/app.py
RUN useradd --uid 10001 --no-create-home demo
USER 10001
EXPOSE 8080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8080"]
```

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `uv run pytest apps/api/tests/unit -v`
Expected: PASS — four demo-target tests.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -s -m "feat(demo-target): latency-shaped sample system under test"
```

---

### Task 7: Compose stack, API image, and `make dev`

**Files:**
- Create: `infrastructure/docker/docker-compose.yml`, `infrastructure/docker/api.Dockerfile`, `infrastructure/docker/postgres-init/01-roles.sql`, `.env.example`
- Create: `apps/api/plimsoll_api/readiness.py`
- Modify: `Makefile`, `apps/api/plimsoll_api/main.py`
- Test: `apps/api/tests/integration/test_stack.py`

**Interfaces:**
- Consumes: `set_readiness_checks` (Task 5), `get_settings` (Task 3).
- Produces: services `postgres`, `redis`, `minio`, `api`, `demo-target` on network `plimsoll`; roles `plimsoll_owner` and `plimsoll_app`; `make dev`, `make dev-down`, `make test-int`.

- [ ] **Step 1: Write the failing integration test**

`apps/api/tests/integration/test_stack.py`:

```python
import os

import httpx
import pytest

pytestmark = pytest.mark.integration

API = os.environ.get("PLIMSOLL_TEST_API_URL", "http://localhost:8000")
TARGET = os.environ.get("PLIMSOLL_TEST_TARGET_URL", "http://localhost:8080")


def test_api_is_live() -> None:
    assert httpx.get(f"{API}/healthz", timeout=5).json() == {"status": "ok"}


def test_api_is_ready_with_every_dependency_up() -> None:
    response = httpx.get(f"{API}/readyz", timeout=10)
    assert response.status_code == 200
    checks = response.json()["checks"]
    assert checks == {"postgres": True, "redis": True, "objectstore": True}


def test_version_is_served() -> None:
    assert httpx.get(f"{API}/api/v1/version", timeout=5).json()["version"]


def test_demo_target_responds() -> None:
    assert httpx.get(f"{TARGET}/healthz", timeout=5).status_code == 200
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/integration -v -m integration`
Expected: FAIL — `httpx.ConnectError`, nothing is listening.

- [ ] **Step 3: Write the postgres role init script**

`infrastructure/docker/postgres-init/01-roles.sql`. The official image runs files in `/docker-entrypoint-initdb.d` as superuser on first initialisation only.

```sql
-- plimsoll_owner owns the schema and runs migrations.
-- plimsoll_app is the runtime role. It owns nothing, so row-level security
-- applies to it -- PostgreSQL never applies policies to a table's owner.
CREATE ROLE plimsoll_owner LOGIN PASSWORD 'plimsoll_owner_dev';
CREATE ROLE plimsoll_app   LOGIN PASSWORD 'plimsoll_app_dev';

ALTER DATABASE plimsoll OWNER TO plimsoll_owner;

\connect plimsoll
GRANT USAGE ON SCHEMA public TO plimsoll_app;
ALTER SCHEMA public OWNER TO plimsoll_owner;
```

- [ ] **Step 4: Write `api.Dockerfile`**

```dockerfile
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY --from=ghcr.io/astral-sh/uv:0.4.20 /uv /usr/local/bin/uv
WORKDIR /srv

COPY pyproject.toml uv.lock ./
COPY apps/api/pyproject.toml apps/api/
COPY packages/contracts/python/pyproject.toml packages/contracts/python/
RUN uv sync --frozen --no-dev --no-install-project

COPY packages/contracts/python packages/contracts/python
COPY apps/api apps/api
RUN uv sync --frozen --no-dev

RUN useradd --uid 10001 --no-create-home plimsoll
USER 10001
EXPOSE 8000
CMD ["uv", "run", "uvicorn", "plimsoll_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 5: Write `docker-compose.yml`**

```yaml
name: plimsoll

services:
  postgres:
    image: timescale/timescaledb:2.16.1-pg16
    environment:
      POSTGRES_DB: plimsoll
      POSTGRES_PASSWORD: postgres_dev
    volumes:
      - ./postgres-init:/docker-entrypoint-initdb.d:ro
      - pgdata:/var/lib/postgresql/data
    ports: ["5432:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d plimsoll"]
      interval: 3s
      retries: 20

  redis:
    image: redis:7-alpine
    command: ["redis-server", "--appendonly", "yes", "--appendfsync", "everysec"]
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 3s
      retries: 20

  minio:
    image: minio/minio:RELEASE.2024-09-13T20-26-02Z
    command: ["server", "/data", "--console-address", ":9001"]
    environment:
      MINIO_ROOT_USER: plimsoll
      MINIO_ROOT_PASSWORD: plimsoll_dev_secret
    ports: ["9000:9000", "9001:9001"]
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 5s
      retries: 20

  api:
    build:
      context: ../..
      dockerfile: infrastructure/docker/api.Dockerfile
    environment:
      PLIMSOLL_DATABASE_URL: postgresql+asyncpg://plimsoll_app:plimsoll_app_dev@postgres:5432/plimsoll
      PLIMSOLL_MIGRATION_DATABASE_URL: postgresql+asyncpg://plimsoll_owner:plimsoll_owner_dev@postgres:5432/plimsoll
      PLIMSOLL_REDIS_URL: redis://redis:6379/0
      PLIMSOLL_S3_ENDPOINT: http://minio:9000
      PLIMSOLL_JWT_SECRET: development-only-secret-change-me
      PLIMSOLL_ENVIRONMENT: development
    ports: ["8000:8000"]
    depends_on:
      postgres: {condition: service_healthy}
      redis: {condition: service_healthy}
      minio: {condition: service_healthy}

  demo-target:
    build:
      context: ../..
      dockerfile: infrastructure/docker/demo-target.Dockerfile
    ports: ["8080:8080"]

volumes:
  pgdata:
```

- [ ] **Step 6: Implement `readiness.py`**

```python
from __future__ import annotations

import httpx
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class PostgresCheck:
    name = "postgres"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def check(self) -> bool:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False


class RedisCheck:
    name = "redis"

    def __init__(self, url: str) -> None:
        self._url = url

    async def check(self) -> bool:
        try:
            client = aioredis.from_url(self._url)
            await client.ping()
            await client.aclose()
            return True
        except Exception:
            return False


class ObjectStoreCheck:
    name = "objectstore"

    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint

    async def check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(f"{self._endpoint}/minio/health/live")
            return response.status_code == 200
        except Exception:
            return False
```

Add `redis>=5.0`, `httpx>=0.27`, `sqlalchemy[asyncio]>=2.0`, `asyncpg>=0.29` to `apps/api/pyproject.toml`. Wire the checks in `main.py` after including the health router:

```python
    from plimsoll_api.db.session import get_engine
    from plimsoll_api.readiness import ObjectStoreCheck, PostgresCheck, RedisCheck
    from plimsoll_api.routers.health import set_readiness_checks

    set_readiness_checks(
        app,
        [
            PostgresCheck(get_engine()),
            RedisCheck(settings.redis_url),
            ObjectStoreCheck(settings.s3_endpoint),
        ],
    )
```

`get_engine` arrives in Task 11. Until then define it minimally in `apps/api/plimsoll_api/db/session.py`:

```python
from __future__ import annotations

from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from plimsoll_api.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)
```

Add `migration_database_url: str` to `Settings` in `config.py`. It is a new
**required** setting, so also add a line to `apps/api/tests/conftest.py` or every
unit test that builds the app will start failing:

```python
os.environ.setdefault(
    "PLIMSOLL_MIGRATION_DATABASE_URL", "postgresql+asyncpg://o:p@localhost/plimsoll"
)
```

Run `uv run pytest apps/api/tests/unit -v` after this change and confirm the
existing unit tests still pass before moving on.

- [ ] **Step 7: Extend the Makefile**

```makefile
COMPOSE := docker compose -f infrastructure/docker/docker-compose.yml

.PHONY: dev dev-down test-int

dev:
	$(COMPOSE) up --build -d
	$(COMPOSE) exec -T api uv run alembic -c apps/api/alembic.ini upgrade head || true
	@echo "Plimsoll is up. API http://localhost:8000  demo target http://localhost:8080"

dev-down:
	$(COMPOSE) down -v

test-int:
	$(UV) pytest apps/api/tests/integration -v -m integration
```

The `|| true` on migration is temporary and removed in Task 9, when a migration exists.

- [ ] **Step 8: Run the stack and the integration tests**

Run: `make dev && sleep 10 && make test-int`
Expected: PASS — four stack tests. If `readyz` reports `objectstore: false`, check the MinIO health path is reachable from the API container.

- [ ] **Step 9: Write `.env.example`**

```bash
PLIMSOLL_DATABASE_URL=postgresql+asyncpg://plimsoll_app:plimsoll_app_dev@localhost:5432/plimsoll
PLIMSOLL_MIGRATION_DATABASE_URL=postgresql+asyncpg://plimsoll_owner:plimsoll_owner_dev@localhost:5432/plimsoll
PLIMSOLL_REDIS_URL=redis://localhost:6379/0
PLIMSOLL_S3_ENDPOINT=http://localhost:9000
PLIMSOLL_JWT_SECRET=change-me
PLIMSOLL_LOG_LEVEL=INFO
```

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -s -m "feat(infra): compose stack, API image, and make dev"
```

---

### Task 8: Alembic scaffolding and the TimescaleDB extension

**Files:**
- Create: `apps/api/alembic.ini`, `apps/api/plimsoll_api/migrations/env.py`, `apps/api/plimsoll_api/migrations/script.py.mako`, `apps/api/plimsoll_api/migrations/versions/0001_extensions.py`
- Create: `apps/api/plimsoll_api/db/base.py`
- Test: `apps/api/tests/integration/test_migrations.py`

**Interfaces:**
- Consumes: `get_settings` (Task 3).
- Produces: `Base` declarative class in `db/base.py`; migrations run with `alembic -c apps/api/alembic.ini upgrade head` as `plimsoll_owner`; revision id `0001_extensions`.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/integration/test_migrations.py`:

```python
import os

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration

OWNER_URL = os.environ.get(
    "PLIMSOLL_TEST_MIGRATION_URL",
    "postgresql+psycopg://plimsoll_owner:plimsoll_owner_dev@localhost:5432/plimsoll",
)


def test_timescaledb_extension_is_installed() -> None:
    engine = sa.create_engine(OWNER_URL)
    with engine.connect() as connection:
        result = connection.execute(
            sa.text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
        ).scalar()
    assert result == 1


def test_alembic_is_at_head() -> None:
    engine = sa.create_engine(OWNER_URL)
    with engine.connect() as connection:
        version = connection.execute(
            sa.text("SELECT version_num FROM alembic_version")
        ).scalar()
    assert version is not None
```

Add `psycopg[binary]>=3.1` to the dev dependency group — the synchronous driver keeps migration tests simple.

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/integration/test_migrations.py -v -m integration`
Expected: FAIL — relation `alembic_version` does not exist.

- [ ] **Step 3: Add Alembic and create `alembic.ini`**

Add `alembic>=1.13` to `apps/api/pyproject.toml`. Create `apps/api/alembic.ini`:

```ini
[alembic]
script_location = apps/api/plimsoll_api/migrations
prepend_sys_path = apps/api
file_template = %%(rev)s_%%(slug)s

[loggers]
keys = root

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console

[handler_console]
class = StreamHandler
args = (sys.stderr,)
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

- [ ] **Step 4: Create `db/base.py` and `migrations/env.py`**

`db/base.py`:

```python
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

`migrations/env.py` — note it connects as the **owner**, never the app role:

```python
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from plimsoll_api.config import get_settings
from plimsoll_api.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _sync_url() -> str:
    # Alembic runs synchronously; swap the async driver for psycopg.
    return get_settings().migration_database_url.replace("+asyncpg", "+psycopg")


def run_migrations_offline() -> None:
    context.configure(url=_sync_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_sync_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

Copy the standard `script.py.mako` from Alembic's templates.

- [ ] **Step 5: Write the extensions migration**

`migrations/versions/0001_extensions.py`:

```python
"""Extensions and baseline grants.

Revision ID: 0001_extensions
Revises:
"""

from __future__ import annotations

from alembic import op

revision = "0001_extensions"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    # Anything plimsoll_owner creates from here on is usable by the runtime role.
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE plimsoll_owner IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO plimsoll_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE plimsoll_owner IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO plimsoll_app"
    )


def downgrade() -> None:
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE plimsoll_owner IN SCHEMA public "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM plimsoll_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES FOR ROLE plimsoll_owner IN SCHEMA public "
        "REVOKE USAGE, SELECT ON SEQUENCES FROM plimsoll_app"
    )
    op.execute("DROP EXTENSION IF EXISTS timescaledb")
```

- [ ] **Step 6: Run the migration and the tests**

Run: `make dev && docker compose -f infrastructure/docker/docker-compose.yml exec -T api uv run alembic -c apps/api/alembic.ini upgrade head && make test-int`
Expected: PASS — both migration tests.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -s -m "feat(db): alembic scaffolding, TimescaleDB extension, default grants"
```

---

### Task 9: The initial schema

**Files:**
- Create: `apps/api/plimsoll_api/db/models/identity.py`, `.../projects.py`, `.../scripts.py`, `.../tests.py`, `.../runs.py`, `.../platform.py`
- Create: `apps/api/plimsoll_api/migrations/versions/0002_initial_schema.py`
- Test: `apps/api/tests/integration/test_schema.py`

**Interfaces:**
- Consumes: `Base` (Task 8).
- Produces: all twenty-three tables plus the `performance_metrics` hypertable; SQLAlchemy models importable from `plimsoll_api.db.models`.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/integration/test_schema.py`:

```python
import os

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration

OWNER_URL = os.environ.get(
    "PLIMSOLL_TEST_MIGRATION_URL",
    "postgresql+psycopg://plimsoll_owner:plimsoll_owner_dev@localhost:5432/plimsoll",
)

EXPECTED_TABLES = {
    "organizations", "users", "project_members", "projects", "credentials",
    "script_repos", "script_versions", "performance_tests",
    "performance_test_plans", "sla_rules", "generator_pools", "test_runs",
    "run_generators", "run_errors", "baselines", "idempotency_keys",
    "api_keys", "webhook_subscriptions", "audit_logs", "performance_metrics",
    "target_policies", "refresh_token_families", "project_run_counters",
}


def _inspector() -> sa.Inspector:
    return sa.inspect(sa.create_engine(OWNER_URL))


def test_every_table_exists() -> None:
    assert EXPECTED_TABLES <= set(_inspector().get_table_names())


def test_performance_metrics_is_a_hypertable() -> None:
    engine = sa.create_engine(OWNER_URL)
    with engine.connect() as connection:
        count = connection.execute(
            sa.text(
                "SELECT count(*) FROM timescaledb_information.hypertables "
                "WHERE hypertable_name = 'performance_metrics'"
            )
        ).scalar()
    assert count == 1


def test_every_tenant_table_carries_organization_id() -> None:
    inspector = _inspector()
    exempt = {"organizations", "project_members"}
    for table in EXPECTED_TABLES - exempt:
        columns = {c["name"] for c in inspector.get_columns(table)}
        assert "organization_id" in columns, f"{table} cannot be protected by RLS"
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/integration/test_schema.py -v -m integration`
Expected: FAIL — the expected tables are absent.

- [ ] **Step 3: Write the models**

Follow [the data model](../../architecture/04-data-model.md) exactly for columns, types, and constraints. One module per concern, each importing `Base` from `plimsoll_api.db.base`. Example, `db/models/identity.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from plimsoll_api.db.base import Base


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("organization_id", "email"),)

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="ACTIVE")
    org_role: Mapped[str] = mapped_column(String(50), nullable=False, server_default="VIEWER")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
```

Write the remaining models the same way, grouped as: `projects.py` (`projects`, `project_members`, `project_run_counters`), `scripts.py` (`credentials`, `script_repos`, `script_versions`), `tests.py` (`performance_tests`, `performance_test_plans`, `sla_rules`), `runs.py` (`test_runs`, `run_generators`, `run_errors`, `baselines`, `generator_pools`), `platform.py` (`api_keys`, `idempotency_keys`, `audit_logs`, `webhook_subscriptions`, `target_policies`, `refresh_token_families`). Re-export every model from `db/models/__init__.py`.

`performance_metrics` has no ORM model — it is written by the metrics worker in slice 4 and defined in raw SQL in the migration.

- [ ] **Step 4: Generate and hand-finish the migration**

Run: `docker compose -f infrastructure/docker/docker-compose.yml exec -T api uv run alembic -c apps/api/alembic.ini revision --autogenerate -m "initial schema"`

Rename the file to `0002_initial_schema.py`, set `revision = "0002_initial_schema"` and `down_revision = "0001_extensions"`. Autogenerate cannot see the hypertable, so append to `upgrade()`:

```python
    op.execute(
        """
        CREATE TABLE performance_metrics (
            time             TIMESTAMPTZ      NOT NULL,
            organization_id  UUID             NOT NULL,
            run_id           UUID             NOT NULL,
            metric_name      VARCHAR(255)     NOT NULL,
            metric_kind      VARCHAR(20)      NOT NULL,
            entity_type      VARCHAR(100),
            entity_id        VARCHAR(255),
            value            DOUBLE PRECISION,
            sketch           BYTEA,
            tags             JSONB
        )
        """
    )
    op.execute("SELECT create_hypertable('performance_metrics', 'time')")
    op.execute(
        "CREATE INDEX idx_metrics_run_time ON performance_metrics (run_id, time DESC)"
    )
```

and to `downgrade()`, before the autogenerated drops:

```python
    op.execute("DROP TABLE IF EXISTS performance_metrics")
```

- [ ] **Step 5: Run migration up, then down, then up**

Run:
```bash
DC="docker compose -f infrastructure/docker/docker-compose.yml"
$DC exec -T api uv run alembic -c apps/api/alembic.ini upgrade head
$DC exec -T api uv run alembic -c apps/api/alembic.ini downgrade 0001_extensions
$DC exec -T api uv run alembic -c apps/api/alembic.ini upgrade head
```
Expected: all three succeed. A failure on downgrade means an irreversible migration, which must be fixed now.

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `make test-int`
Expected: PASS — three schema tests.

- [ ] **Step 7: Remove the temporary `|| true` from the `dev` target in the Makefile**

Migrations now exist and must not fail silently.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -s -m "feat(db): initial schema, twenty-three tables and the metrics hypertable"
```

---

### Task 10: Row-level security and the isolation proof

**Files:**
- Create: `apps/api/plimsoll_api/migrations/versions/0003_row_level_security.py`
- Test: `apps/api/tests/integration/test_tenant_isolation.py`

**Interfaces:**
- Consumes: the schema (Task 9).
- Produces: `ENABLE`/`FORCE ROW LEVEL SECURITY` and a `tenant_isolation` policy on every tenant table; the `auth_lookup_user(text)` function.

**This is the load-bearing task of the slice.** If it is wrong, invariant 4 is decorative and every other test still passes.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/integration/test_tenant_isolation.py`:

```python
import os
import uuid

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration

OWNER_URL = os.environ.get(
    "PLIMSOLL_TEST_MIGRATION_URL",
    "postgresql+psycopg://plimsoll_owner:plimsoll_owner_dev@localhost:5432/plimsoll",
)
APP_URL = os.environ.get(
    "PLIMSOLL_TEST_APP_URL",
    "postgresql+psycopg://plimsoll_app:plimsoll_app_dev@localhost:5432/plimsoll",
)


@pytest.fixture
def two_organisations() -> tuple[uuid.UUID, uuid.UUID]:
    left, right = uuid.uuid4(), uuid.uuid4()
    engine = sa.create_engine(OWNER_URL)
    with engine.begin() as connection:
        for org_id, slug in ((left, f"left-{left.hex[:8]}"), (right, f"right-{right.hex[:8]}")):
            connection.execute(
                sa.text(
                    "INSERT INTO organizations (id, name, slug) VALUES (:id, :name, :slug)"
                ),
                {"id": org_id, "name": slug, "slug": slug},
            )
            connection.execute(
                sa.text(
                    "INSERT INTO projects (id, organization_id, name, project_key) "
                    "VALUES (:id, :org, :name, :key)"
                ),
                {"id": uuid.uuid4(), "org": org_id, "name": slug, "key": slug[:20]},
            )
    yield left, right
    with engine.begin() as connection:
        for org_id in (left, right):
            connection.execute(
                sa.text("DELETE FROM projects WHERE organization_id = :org"), {"org": org_id}
            )
            connection.execute(sa.text("DELETE FROM organizations WHERE id = :org"), {"org": org_id})


def _projects_visible_to(org_id: uuid.UUID | None) -> list[uuid.UUID]:
    engine = sa.create_engine(APP_URL)
    with engine.begin() as connection:
        if org_id is not None:
            connection.execute(
                sa.text("SELECT set_config('app.current_org_id', :org, true)"),
                {"org": str(org_id)},
            )
        rows = connection.execute(sa.text("SELECT organization_id FROM projects")).scalars().all()
    return list(rows)


def test_an_organisation_sees_only_its_own_rows(two_organisations) -> None:
    left, right = two_organisations
    assert set(_projects_visible_to(left)) == {left}
    assert set(_projects_visible_to(right)) == {right}


def test_without_the_setting_nothing_is_visible(two_organisations) -> None:
    assert _projects_visible_to(None) == []


def test_the_runtime_role_is_not_the_table_owner() -> None:
    engine = sa.create_engine(APP_URL)
    with engine.connect() as connection:
        owner = connection.execute(
            sa.text("SELECT tableowner FROM pg_tables WHERE tablename = 'projects'")
        ).scalar()
        current = connection.execute(sa.text("SELECT current_user")).scalar()
    assert owner != current, "RLS does not apply to a table's owner"


def test_row_level_security_is_forced_on_every_tenant_table() -> None:
    engine = sa.create_engine(OWNER_URL)
    with engine.connect() as connection:
        rows = connection.execute(
            sa.text(
                "SELECT relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'r' "
                "AND c.relrowsecurity AND NOT c.relforcerowsecurity"
            )
        ).scalars().all()
    assert list(rows) == [], f"RLS enabled but not forced on: {rows}"


def test_auth_lookup_user_exposes_only_the_login_columns() -> None:
    engine = sa.create_engine(APP_URL)
    with engine.connect() as connection:
        columns = connection.execute(
            sa.text(
                "SELECT proargnames FROM pg_proc WHERE proname = 'auth_lookup_user'"
            )
        ).scalar()
    assert set(columns) >= {"id", "organization_id", "password_hash", "status", "org_role"}
    assert "name" not in set(columns)
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/integration/test_tenant_isolation.py -v -m integration`
Expected: FAIL — `test_an_organisation_sees_only_its_own_rows` sees both organisations' rows, because no policy exists yet. That failure is the whole point of the task.

- [ ] **Step 3: Write the migration**

`migrations/versions/0003_row_level_security.py`:

```python
"""Row-level security.

PostgreSQL does not apply policies to a table's owner and never to a
superuser, so the runtime role must own nothing and every table is FORCEd.

Revision ID: 0003_row_level_security
Revises: 0002_initial_schema
"""

from __future__ import annotations

from alembic import op

revision = "0003_row_level_security"
down_revision = "0002_initial_schema"
branch_labels = None
depends_on = None

TENANT_TABLES = [
    "users", "projects", "project_run_counters", "credentials", "script_repos",
    "script_versions", "performance_tests", "performance_test_plans", "sla_rules",
    "generator_pools", "test_runs", "run_generators", "run_errors", "baselines",
    "idempotency_keys", "api_keys", "webhook_subscriptions", "audit_logs",
    "target_policies", "refresh_token_families", "performance_metrics",
]

ORG_PREDICATE = (
    "organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid"
)


def upgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({ORG_PREDICATE}) WITH CHECK ({ORG_PREDICATE})"
        )

    # organizations is keyed on its own primary key.
    op.execute("ALTER TABLE organizations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE organizations FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON organizations "
        "USING (id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)"
    )

    # project_members reaches its organisation through the project.
    op.execute("ALTER TABLE project_members ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE project_members FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON project_members USING ("
        "  EXISTS (SELECT 1 FROM projects p WHERE p.id = project_members.project_id))"
    )

    # Login must find a user before an organisation is known. This is the one
    # deliberate exception, and it returns nothing but the login columns.
    op.execute(
        """
        CREATE FUNCTION auth_lookup_user(p_email text)
        RETURNS TABLE (id uuid, organization_id uuid, password_hash text,
                       status text, org_role text)
        LANGUAGE sql SECURITY DEFINER SET search_path = public STABLE AS $$
            SELECT id, organization_id, password_hash, status, org_role
            FROM users WHERE email = lower(p_email);
        $$
        """
    )
    op.execute("GRANT EXECUTE ON FUNCTION auth_lookup_user(text) TO plimsoll_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS auth_lookup_user(text)")
    for table in [*TENANT_TABLES, "organizations", "project_members"]:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
```

- [ ] **Step 4: Apply and run the tests**

Run:
```bash
DC="docker compose -f infrastructure/docker/docker-compose.yml"
$DC exec -T api uv run alembic -c apps/api/alembic.ini upgrade head
make test-int
```
Expected: PASS — all five isolation tests.

- [ ] **Step 5: Prove the test would catch a regression**

Temporarily drop one policy and confirm the suite fails:

```bash
$DC exec -T postgres psql -U plimsoll_owner -d plimsoll -c "DROP POLICY tenant_isolation ON projects"
make test-int   # expect test_an_organisation_sees_only_its_own_rows to FAIL
$DC exec -T api uv run alembic -c apps/api/alembic.ini downgrade 0002_initial_schema
$DC exec -T api uv run alembic -c apps/api/alembic.ini upgrade head
make test-int   # expect PASS again
```

A test that cannot fail is not protecting anything. Do not skip this step.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -s -m "feat(db): row-level security with forced policies and isolation proof"
```

---

### Task 11: Request-scoped session carrying the organisation

**Files:**
- Modify: `apps/api/plimsoll_api/db/session.py`
- Test: `apps/api/tests/integration/test_session_scope.py`

**Interfaces:**
- Consumes: `get_settings` (Task 3), the policies (Task 10).
- Produces: `get_engine() -> AsyncEngine`; `async def session_for_org(org_id: uuid.UUID | None) -> AsyncIterator[AsyncSession]` context manager that opens a transaction and applies the setting; FastAPI dependency `db_session`.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/integration/test_session_scope.py`:

```python
import uuid

import pytest
import sqlalchemy as sa

from plimsoll_api.db.session import session_for_org

pytestmark = pytest.mark.integration


async def _current_setting(session) -> str:
    return await session.scalar(
        sa.text("SELECT current_setting('app.current_org_id', true)")
    )


async def test_the_setting_is_applied_inside_the_transaction() -> None:
    org_id = uuid.uuid4()
    async with session_for_org(org_id) as session:
        assert await _current_setting(session) == str(org_id)


async def test_the_setting_does_not_leak_to_the_next_session() -> None:
    first = uuid.uuid4()
    async with session_for_org(first) as session:
        assert await _current_setting(session) == str(first)

    async with session_for_org(None) as session:
        assert await _current_setting(session) in ("", None)
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/integration/test_session_scope.py -v -m integration`
Expected: FAIL — `ImportError: cannot import name 'session_for_org'`.

- [ ] **Step 3: Implement the session module**

```python
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from plimsoll_api.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def _session_factory() -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(), expire_on_commit=False)


@asynccontextmanager
async def session_for_org(org_id: uuid.UUID | None) -> AsyncIterator[AsyncSession]:
    """One transaction per request, with the tenant setting applied inside it.

    set_config(..., true) is transaction-local, so a pooled connection cannot
    carry the value into the next request. It is used in preference to
    SET LOCAL because SET does not accept bind parameters.
    """
    async with _session_factory()() as session:
        async with session.begin():
            if org_id is not None:
                await session.execute(
                    text("SELECT set_config('app.current_org_id', :org, true)"),
                    {"org": str(org_id)},
                )
            yield session
```

- [ ] **Step 4: Run the tests and make sure they pass**

Run: `make test-int`
Expected: PASS — both scope tests.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -s -m "feat(db): request-scoped session applying the tenant setting"
```

---

### Task 12: Password hashing and access tokens

**Files:**
- Create: `apps/api/plimsoll_api/security/__init__.py`, `apps/api/plimsoll_api/security/passwords.py`, `apps/api/plimsoll_api/security/tokens.py`
- Test: `apps/api/tests/unit/test_passwords.py`, `apps/api/tests/unit/test_tokens.py`

**Interfaces:**
- Consumes: `get_settings` (Task 3).
- Produces: `hash_password(str) -> str`, `verify_password(hash: str, password: str) -> bool`, `needs_rehash(str) -> bool`; `issue_access_token(user_id: uuid.UUID, org_id: uuid.UUID, role: str) -> str`, `decode_access_token(str) -> AccessClaims` raising `TokenError`; `AccessClaims` dataclass with `user_id`, `organization_id`, `role`.

- [ ] **Step 1: Write the failing tests**

`apps/api/tests/unit/test_passwords.py`:

```python
from plimsoll_api.security.passwords import hash_password, verify_password


def test_hash_is_argon2id_and_salted() -> None:
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")
    assert first.startswith("$argon2id$")
    assert first != second


def test_verification_accepts_the_right_password() -> None:
    stored = hash_password("s3cret")
    assert verify_password(stored, "s3cret") is True


def test_verification_rejects_the_wrong_password() -> None:
    stored = hash_password("s3cret")
    assert verify_password(stored, "not-it") is False


def test_verification_rejects_a_malformed_hash() -> None:
    assert verify_password("not-a-hash", "anything") is False
```

`apps/api/tests/unit/test_tokens.py`:

```python
import uuid

import pytest

from plimsoll_api.security.tokens import TokenError, decode_access_token, issue_access_token


def test_round_trip_preserves_the_principal() -> None:
    user_id, org_id = uuid.uuid4(), uuid.uuid4()
    claims = decode_access_token(issue_access_token(user_id, org_id, "ORG_ADMIN"))
    assert claims.user_id == user_id
    assert claims.organization_id == org_id
    assert claims.role == "ORG_ADMIN"


def test_a_tampered_token_is_rejected() -> None:
    token = issue_access_token(uuid.uuid4(), uuid.uuid4(), "VIEWER")
    head, payload, signature = token.split(".")
    with pytest.raises(TokenError):
        decode_access_token(f"{head}.{payload}.{signature[:-2]}xx")


def test_an_expired_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLIMSOLL_ACCESS_TOKEN_TTL_SECONDS", "-1")
    from plimsoll_api.config import get_settings

    get_settings.cache_clear()
    token = issue_access_token(uuid.uuid4(), uuid.uuid4(), "VIEWER")
    with pytest.raises(TokenError):
        decode_access_token(token)
    get_settings.cache_clear()
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `uv run pytest apps/api/tests/unit/test_passwords.py apps/api/tests/unit/test_tokens.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Add dependencies**

Add `argon2-cffi>=23.1` and `pyjwt>=2.8` to `apps/api/pyproject.toml`, then `uv sync`.

- [ ] **Step 4: Implement `passwords.py`**

```python
from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(stored_hash, password)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored_hash)
    except (InvalidHashError, ValueError):
        return True
```

- [ ] **Step 5: Implement `tokens.py`**

```python
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from plimsoll_api.config import get_settings

ALGORITHM = "HS256"


class TokenError(Exception):
    """Raised when a token is absent, malformed, tampered with, or expired."""


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    role: str


def issue_access_token(user_id: uuid.UUID, org_id: uuid.UUID, role: str) -> str:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "org": str(org_id),
        "role": role,
        "iat": now,
        "exp": now + timedelta(seconds=settings.access_token_ttl_seconds),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> AccessClaims:
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    return AccessClaims(
        user_id=uuid.UUID(payload["sub"]),
        organization_id=uuid.UUID(payload["org"]),
        role=payload["role"],
    )
```

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `uv run pytest apps/api/tests/unit -v`
Expected: PASS — seven new tests.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -s -m "feat(security): Argon2id hashing and JWT access tokens"
```

---

### Task 13: Refresh-token rotation with family revocation

**Files:**
- Create: `apps/api/plimsoll_api/repositories/refresh_tokens.py`, `apps/api/plimsoll_api/services/refresh.py`
- Test: `apps/api/tests/integration/test_refresh_rotation.py`

**Interfaces:**
- Consumes: `session_for_org` (Task 11), the `refresh_token_families` table (Task 9).
- Produces: `async def issue_family(session, user_id, org_id) -> str` returning the raw token; `async def rotate(session, raw_token) -> tuple[str, uuid.UUID, uuid.UUID]` returning `(new_raw_token, user_id, org_id)` and raising `RefreshRejected` on reuse, revocation, or expiry.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/integration/test_refresh_rotation.py`:

```python
import uuid

import pytest
import sqlalchemy as sa

from plimsoll_api.db.session import session_for_org
from plimsoll_api.services.refresh import RefreshRejected, issue_family, rotate

pytestmark = pytest.mark.integration


@pytest.fixture
async def user() -> tuple[uuid.UUID, uuid.UUID]:
    org_id, user_id = uuid.uuid4(), uuid.uuid4()
    async with session_for_org(None) as session:
        await session.execute(
            sa.text("SET LOCAL ROLE plimsoll_owner")
        )  # fixture setup only
    async with session_for_org(org_id) as session:
        await session.execute(
            sa.text(
                "INSERT INTO organizations (id, name, slug) VALUES (:id, :n, :s) "
                "ON CONFLICT DO NOTHING"
            ),
            {"id": org_id, "n": "t", "s": f"t-{org_id.hex[:8]}"},
        )
        await session.execute(
            sa.text(
                "INSERT INTO users (id, organization_id, email, name, org_role) "
                "VALUES (:id, :org, :email, 'T', 'VIEWER')"
            ),
            {"id": user_id, "org": org_id, "email": f"{user_id.hex[:8]}@example.com"},
        )
    return user_id, org_id


async def test_rotation_returns_a_new_token(user) -> None:
    user_id, org_id = user
    async with session_for_org(org_id) as session:
        first = await issue_family(session, user_id, org_id)
        second, returned_user, returned_org = await rotate(session, first)
    assert second != first
    assert returned_user == user_id
    assert returned_org == org_id


async def test_reusing_a_consumed_token_revokes_the_family(user) -> None:
    user_id, org_id = user
    async with session_for_org(org_id) as session:
        first = await issue_family(session, user_id, org_id)
        second, _, _ = await rotate(session, first)

        with pytest.raises(RefreshRejected):
            await rotate(session, first)

    # The whole family is dead, so the token that was valid a moment ago fails too.
    async with session_for_org(org_id) as session:
        with pytest.raises(RefreshRejected):
            await rotate(session, second)


async def test_an_unknown_token_is_rejected(user) -> None:
    _, org_id = user
    async with session_for_org(org_id) as session:
        with pytest.raises(RefreshRejected):
            await rotate(session, "not-a-real-token")
```

Note: the `SET LOCAL ROLE` line in the fixture is a setup convenience only and must be removed if it fails under the app role; insert fixture rows through the owner connection used in Task 10's fixture instead.

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/integration/test_refresh_rotation.py -v -m integration`
Expected: FAIL — `ModuleNotFoundError: No module named 'plimsoll_api.services.refresh'`.

- [ ] **Step 3: Implement the repository**

`repositories/refresh_tokens.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def insert_family(
    session: AsyncSession,
    family_id: uuid.UUID,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    token_hash: str,
    expires_at: datetime,
) -> None:
    await session.execute(
        sa.text(
            "INSERT INTO refresh_token_families "
            "(id, organization_id, user_id, current_hash, expires_at) "
            "VALUES (:id, :org, :user, :hash, :expires)"
        ),
        {
            "id": family_id,
            "org": org_id,
            "user": user_id,
            "hash": token_hash,
            "expires": expires_at,
        },
    )


async def find_by_hash(session: AsyncSession, token_hash: str) -> sa.Row | None:
    return (
        await session.execute(
            sa.text(
                "SELECT id, user_id, organization_id, current_hash, revoked_at, expires_at "
                "FROM refresh_token_families WHERE current_hash = :hash"
            ),
            {"hash": token_hash},
        )
    ).first()


async def find_family(session: AsyncSession, family_id: uuid.UUID) -> sa.Row | None:
    return (
        await session.execute(
            sa.text(
                "SELECT id, revoked_at FROM refresh_token_families WHERE id = :id"
            ),
            {"id": family_id},
        )
    ).first()


async def replace_hash(
    session: AsyncSession, family_id: uuid.UUID, token_hash: str
) -> None:
    await session.execute(
        sa.text(
            "UPDATE refresh_token_families SET current_hash = :hash WHERE id = :id"
        ),
        {"hash": token_hash, "id": family_id},
    )


async def revoke_family_containing(session: AsyncSession, token_hash: str) -> None:
    """Revoke by *previous* hash. Reuse of a consumed token is the theft signal."""
    await session.execute(
        sa.text(
            "UPDATE refresh_token_families SET revoked_at = now() "
            "WHERE id = (SELECT family_id FROM refresh_token_history WHERE token_hash = :hash)"
        ),
        {"hash": token_hash},
    )
```

The reuse check needs history, so add a small table in a new migration `0004_refresh_history`:

```python
revision = "0004_refresh_history"
down_revision = "0003_row_level_security"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE refresh_token_history (
            token_hash       VARCHAR(128) PRIMARY KEY,
            family_id        UUID NOT NULL REFERENCES refresh_token_families(id) ON DELETE CASCADE,
            organization_id  UUID NOT NULL REFERENCES organizations(id),
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("ALTER TABLE refresh_token_history ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE refresh_token_history FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON refresh_token_history USING ("
        "organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid) "
        "WITH CHECK (organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS refresh_token_history")
```

Add `refresh_token_history` to `EXPECTED_TABLES` in `test_schema.py`.

- [ ] **Step 4: Implement the service**

`services/refresh.py`:

```python
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.config import get_settings
from plimsoll_api.repositories import refresh_tokens as repo


class RefreshRejected(Exception):
    """The token was unknown, already consumed, revoked, or expired."""


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


async def _record_history(
    session: AsyncSession, family_id: uuid.UUID, org_id: uuid.UUID, token_hash: str
) -> None:
    await session.execute(
        sa.text(
            "INSERT INTO refresh_token_history (token_hash, family_id, organization_id) "
            "VALUES (:hash, :family, :org)"
        ),
        {"hash": token_hash, "family": family_id, "org": org_id},
    )


async def issue_family(
    session: AsyncSession, user_id: uuid.UUID, org_id: uuid.UUID
) -> str:
    raw = secrets.token_urlsafe(48)
    family_id = uuid.uuid4()
    expires_at = datetime.now(UTC) + timedelta(
        seconds=get_settings().refresh_token_ttl_seconds
    )
    await repo.insert_family(session, family_id, org_id, user_id, _hash(raw), expires_at)
    await _record_history(session, family_id, org_id, _hash(raw))
    return raw


async def rotate(session: AsyncSession, raw_token: str) -> tuple[str, uuid.UUID, uuid.UUID]:
    token_hash = _hash(raw_token)
    row = await repo.find_by_hash(session, token_hash)

    if row is None:
        # Not the live token. If we have ever seen it, it is a replay: kill the family.
        await repo.revoke_family_containing(session, token_hash)
        raise RefreshRejected("Unknown or already-consumed refresh token.")

    if row.revoked_at is not None:
        raise RefreshRejected("This token family has been revoked.")
    if row.expires_at <= datetime.now(UTC):
        raise RefreshRejected("Refresh token expired.")

    new_raw = secrets.token_urlsafe(48)
    await repo.replace_hash(session, row.id, _hash(new_raw))
    await _record_history(session, row.id, row.organization_id, _hash(new_raw))
    return new_raw, row.user_id, row.organization_id
```

- [ ] **Step 5: Apply the migration and run the tests**

Run:
```bash
docker compose -f infrastructure/docker/docker-compose.yml exec -T api \
  uv run alembic -c apps/api/alembic.ini upgrade head
make test-int
```
Expected: PASS — three rotation tests, and `test_schema.py` still passing with the new table.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -s -m "feat(auth): refresh-token rotation with family revocation on reuse"
```

---

### Task 14: Authentication endpoints

**Files:**
- Create: `packages/contracts/python/plimsoll_contracts/auth.py`
- Create: `apps/api/plimsoll_api/repositories/users.py`, `apps/api/plimsoll_api/services/auth.py`, `apps/api/plimsoll_api/routers/auth.py`, `apps/api/plimsoll_api/dependencies.py`
- Modify: `apps/api/plimsoll_api/main.py`
- Test: `apps/api/tests/integration/test_auth_endpoints.py`

**Interfaces:**
- Consumes: everything from Tasks 11–13.
- Produces: `POST /api/v1/auth/login`, `POST /api/v1/auth/refresh`, `POST /api/v1/auth/logout`, `GET /api/v1/auth/me`; dependency `current_principal` returning `AccessClaims`; contracts `LoginRequest`, `TokenResponse`, `MeResponse`.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/integration/test_auth_endpoints.py`:

```python
import os

import httpx
import pytest

pytestmark = pytest.mark.integration

API = os.environ.get("PLIMSOLL_TEST_API_URL", "http://localhost:8000")
ADMIN = {"email": "admin@demo.plimsoll.dev", "password": "plimsoll-demo-password"}


def test_login_returns_an_access_token() -> None:
    response = httpx.post(f"{API}/api/v1/auth/login", json=ADMIN, timeout=10)
    assert response.status_code == 200
    assert response.json()["accessToken"]


def test_login_with_a_wrong_password_is_unauthenticated() -> None:
    response = httpx.post(
        f"{API}/api/v1/auth/login",
        json={**ADMIN, "password": "wrong"},
        timeout=10,
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"


def test_me_requires_a_token() -> None:
    assert httpx.get(f"{API}/api/v1/auth/me", timeout=10).status_code == 401


def test_me_returns_the_principal() -> None:
    token = httpx.post(f"{API}/api/v1/auth/login", json=ADMIN, timeout=10).json()["accessToken"]
    response = httpx.get(
        f"{API}/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == ADMIN["email"]
    assert body["orgRole"] == "ORG_ADMIN"


def test_refresh_rotates_and_reuse_is_rejected() -> None:
    with httpx.Client(base_url=API, timeout=10) as client:
        client.post("/api/v1/auth/login", json=ADMIN)
        first_cookie = client.cookies.get("plimsoll_refresh")
        assert first_cookie

        assert client.post("/api/v1/auth/refresh").status_code == 200
        assert client.cookies.get("plimsoll_refresh") != first_cookie

        client.cookies.set("plimsoll_refresh", first_cookie)
        assert client.post("/api/v1/auth/refresh").status_code == 401
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/integration/test_auth_endpoints.py -v -m integration`
Expected: FAIL — 404 on `/api/v1/auth/login`.

- [ ] **Step 3: Write the contracts**

`packages/contracts/python/plimsoll_contracts/auth.py`:

```python
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    access_token: str = Field(serialization_alias="accessToken")
    token_type: str = Field(default="bearer", serialization_alias="tokenType")
    expires_in: int = Field(serialization_alias="expiresIn")


class MeResponse(BaseModel):
    model_config = ConfigDict(serialize_by_alias=True)

    id: str
    email: str
    name: str
    org_role: str = Field(serialization_alias="orgRole")
    organization_id: str = Field(serialization_alias="organizationId")
```

Add `email-validator>=2.1` to the contracts package dependencies.

- [ ] **Step 4: Write the user repository**

`repositories/users.py` — login goes through the `SECURITY DEFINER` function, because no organisation is known yet:

```python
from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession


async def lookup_for_login(session: AsyncSession, email: str) -> sa.Row | None:
    return (
        await session.execute(
            sa.text(
                "SELECT id, organization_id, password_hash, status, org_role "
                "FROM auth_lookup_user(:email)"
            ),
            {"email": email},
        )
    ).first()


async def get_profile(session: AsyncSession, user_id: uuid.UUID) -> sa.Row | None:
    return (
        await session.execute(
            sa.text(
                "SELECT id, email, name, org_role, organization_id "
                "FROM users WHERE id = :id"
            ),
            {"id": user_id},
        )
    ).first()
```

- [ ] **Step 5: Write the service**

`services/auth.py`:

```python
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.repositories import users as user_repo
from plimsoll_api.security.passwords import verify_password
from plimsoll_api.security.tokens import issue_access_token
from plimsoll_api.services.refresh import issue_family


class AuthenticationFailed(Exception):
    """Credentials did not match, or the account is not active."""


async def authenticate(
    session: AsyncSession, email: str, password: str
) -> tuple[str, str, int]:
    """Returns (access_token, refresh_token, access_ttl_seconds)."""
    from plimsoll_api.config import get_settings

    row = await user_repo.lookup_for_login(session, email)
    if row is None or row.password_hash is None or row.status != "ACTIVE":
        # Hash anyway so a missing user and a wrong password take the same time.
        verify_password("$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$0" * 1, password)
        raise AuthenticationFailed("Email or password is incorrect.")

    if not verify_password(row.password_hash, password):
        raise AuthenticationFailed("Email or password is incorrect.")

    access = issue_access_token(row.id, row.organization_id, row.org_role)
    refresh = await issue_family(session, row.id, row.organization_id)
    return access, refresh, get_settings().access_token_ttl_seconds
```

- [ ] **Step 6: Write the dependency and router**

`dependencies.py`:

```python
from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.db.session import session_for_org
from plimsoll_api.errors import PlimsollError
from plimsoll_api.security.tokens import AccessClaims, TokenError, decode_access_token
from plimsoll_contracts.errors import ErrorCode


def current_principal(request: Request) -> AccessClaims:
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise PlimsollError(ErrorCode.UNAUTHENTICATED, "Authentication is required.")
    try:
        return decode_access_token(header.removeprefix("Bearer "))
    except TokenError as exc:
        raise PlimsollError(
            ErrorCode.UNAUTHENTICATED, "The access token is invalid or has expired."
        ) from exc


async def tenant_session(
    principal: AccessClaims = Depends(current_principal),
) -> AsyncIterator[AsyncSession]:
    async with session_for_org(principal.organization_id) as session:
        yield session


async def anonymous_session() -> AsyncIterator[AsyncSession]:
    async with session_for_org(None) as session:
        yield session
```

`routers/auth.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from plimsoll_api.config import get_settings
from plimsoll_api.db.session import session_for_org
from plimsoll_api.dependencies import anonymous_session, current_principal, tenant_session
from plimsoll_api.errors import PlimsollError
from plimsoll_api.repositories import users as user_repo
from plimsoll_api.security.tokens import AccessClaims, issue_access_token
from plimsoll_api.services.auth import AuthenticationFailed, authenticate
from plimsoll_api.services.refresh import RefreshRejected, rotate
from plimsoll_contracts.auth import LoginRequest, MeResponse, TokenResponse
from plimsoll_contracts.errors import ErrorCode

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

REFRESH_COOKIE = "plimsoll_refresh"


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=get_settings().environment != "development",
        max_age=get_settings().refresh_token_ttl_seconds,
        path="/api/v1/auth",
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    response: Response,
    session: AsyncSession = Depends(anonymous_session),
) -> TokenResponse:
    try:
        access, refresh, ttl = await authenticate(session, body.email, body.password)
    except AuthenticationFailed as exc:
        raise PlimsollError(ErrorCode.UNAUTHENTICATED, str(exc)) from exc
    _set_refresh_cookie(response, refresh)
    return TokenResponse(access_token=access, expires_in=ttl)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(response: Response, request_cookie: str | None = None) -> TokenResponse:
    raise NotImplementedError
```

`refresh` needs the cookie and a session without a principal. Replace the stub with:

```python
from fastapi import Cookie


@router.post("/refresh", response_model=TokenResponse)
async def refresh_tokens(
    response: Response,
    plimsoll_refresh: str | None = Cookie(default=None),
) -> TokenResponse:
    if plimsoll_refresh is None:
        raise PlimsollError(ErrorCode.UNAUTHENTICATED, "No refresh token was presented.")
    async with session_for_org(None) as session:
        try:
            new_token, user_id, org_id = await rotate(session, plimsoll_refresh)
        except RefreshRejected as exc:
            raise PlimsollError(ErrorCode.UNAUTHENTICATED, str(exc)) from exc
        profile = await user_repo.get_profile(session, user_id)
    if profile is None:
        raise PlimsollError(ErrorCode.UNAUTHENTICATED, "The account no longer exists.")
    _set_refresh_cookie(response, new_token)
    settings = get_settings()
    return TokenResponse(
        access_token=issue_access_token(user_id, org_id, profile.org_role),
        expires_in=settings.access_token_ttl_seconds,
    )


@router.post("/logout", status_code=204)
async def logout(response: Response) -> None:
    response.delete_cookie(REFRESH_COOKIE, path="/api/v1/auth")


@router.get("/me", response_model=MeResponse)
async def me(
    principal: AccessClaims = Depends(current_principal),
    session: AsyncSession = Depends(tenant_session),
) -> MeResponse:
    profile = await user_repo.get_profile(session, principal.user_id)
    if profile is None:
        raise PlimsollError(ErrorCode.NOT_FOUND, "The account no longer exists.")
    return MeResponse(
        id=str(profile.id),
        email=profile.email,
        name=profile.name,
        org_role=profile.org_role,
        organization_id=str(profile.organization_id),
    )
```

The `rotate` and `get_profile` calls run with no organisation set, so `get_profile` must be reached through the owner-side lookup. Simplest correct approach: after rotation, re-open a session scoped to `org_id` and read the profile there. Restructure the body accordingly rather than reading `users` with no setting.

Register the router in `main.py`: `app.include_router(auth.router)`.

- [ ] **Step 7: Run the tests and make sure they pass**

Run: `make dev && make test-int`
Expected: FAIL until Task 15 seeds the admin account. Run Task 15 first if the fixtures are missing, then return here — the tests in this task depend on the seed.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -s -m "feat(auth): login, refresh, logout, and me endpoints"
```

---

### Task 15: The seed

**Files:**
- Create: `apps/api/plimsoll_api/seed.py`
- Modify: `Makefile` (`make dev` runs the seed)
- Test: `apps/api/tests/integration/test_seed.py`

**Interfaces:**
- Consumes: the schema (Task 9), `hash_password` (Task 12).
- Produces: `async def seed() -> None`, idempotent; demo organisation `demo`, users `admin@demo.plimsoll.dev` (`ORG_ADMIN`) and `viewer@demo.plimsoll.dev` (`VIEWER`) both with password `plimsoll-demo-password`, a demo project, and `target_policies` version 1 allowlisting `demo-target`.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/integration/test_seed.py`:

```python
import os

import pytest
import sqlalchemy as sa

pytestmark = pytest.mark.integration

OWNER_URL = os.environ.get(
    "PLIMSOLL_TEST_MIGRATION_URL",
    "postgresql+psycopg://plimsoll_owner:plimsoll_owner_dev@localhost:5432/plimsoll",
)


def _scalar(query: str) -> object:
    with sa.create_engine(OWNER_URL).connect() as connection:
        return connection.execute(sa.text(query)).scalar()


def test_demo_organisation_exists() -> None:
    assert _scalar("SELECT count(*) FROM organizations WHERE slug = 'demo'") == 1


def test_both_demo_users_exist_with_the_documented_roles() -> None:
    assert _scalar(
        "SELECT org_role FROM users WHERE email = 'admin@demo.plimsoll.dev'"
    ) == "ORG_ADMIN"
    assert _scalar(
        "SELECT org_role FROM users WHERE email = 'viewer@demo.plimsoll.dev'"
    ) == "VIEWER"


def test_passwords_are_argon2id_hashed() -> None:
    stored = _scalar("SELECT password_hash FROM users WHERE email = 'admin@demo.plimsoll.dev'")
    assert isinstance(stored, str) and stored.startswith("$argon2id$")


def test_target_policy_allows_only_the_demo_target() -> None:
    allowlist = _scalar(
        "SELECT allowlist FROM target_policies WHERE version = 1 "
        "AND organization_id = (SELECT id FROM organizations WHERE slug = 'demo')"
    )
    assert allowlist == ["demo-target"]


def test_seeding_twice_changes_nothing() -> None:
    before = _scalar("SELECT count(*) FROM users")
    os.system(  # noqa: S605
        "docker compose -f infrastructure/docker/docker-compose.yml "
        "exec -T api uv run python -m plimsoll_api.seed"
    )
    assert _scalar("SELECT count(*) FROM users") == before
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/integration/test_seed.py -v -m integration`
Expected: FAIL — no demo organisation.

- [ ] **Step 3: Implement `seed.py`**

It connects as the **owner**, because seeding creates the first organisation and therefore cannot be scoped to one.

```python
"""Idempotent development seed.

Runs as plimsoll_owner: it creates the first organisation, so there is no
organisation to scope the session to.
"""

from __future__ import annotations

import asyncio
import uuid

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from plimsoll_api.config import get_settings
from plimsoll_api.security.passwords import hash_password

DEMO_PASSWORD = "plimsoll-demo-password"  # noqa: S105 - development seed only
DEMO_USERS = [
    ("admin@demo.plimsoll.dev", "Demo Admin", "ORG_ADMIN"),
    ("viewer@demo.plimsoll.dev", "Demo Viewer", "VIEWER"),
]


async def seed() -> None:
    engine = create_async_engine(get_settings().migration_database_url)
    async with engine.begin() as connection:
        org_id = await connection.scalar(
            sa.text("SELECT id FROM organizations WHERE slug = 'demo'")
        )
        if org_id is None:
            org_id = uuid.uuid4()
            await connection.execute(
                sa.text(
                    "INSERT INTO organizations (id, name, slug) "
                    "VALUES (:id, 'Demo organisation', 'demo')"
                ),
                {"id": org_id},
            )

        for email, name, role in DEMO_USERS:
            await connection.execute(
                sa.text(
                    "INSERT INTO users (id, organization_id, email, name, password_hash, org_role) "
                    "VALUES (:id, :org, :email, :name, :hash, :role) "
                    "ON CONFLICT (organization_id, email) DO NOTHING"
                ),
                {
                    "id": uuid.uuid4(),
                    "org": org_id,
                    "email": email,
                    "name": name,
                    "hash": hash_password(DEMO_PASSWORD),
                    "role": role,
                },
            )

        await connection.execute(
            sa.text(
                "INSERT INTO projects (id, organization_id, name, project_key, environment) "
                "VALUES (:id, :org, 'Demo project', 'DEMO', 'development') "
                "ON CONFLICT (organization_id, project_key) DO NOTHING"
            ),
            {"id": uuid.uuid4(), "org": org_id},
        )

        # Empty allowlist permits no runs (ADR-0007). The demo works because it
        # ships its own permitted target, not because the check is soft.
        await connection.execute(
            sa.text(
                "INSERT INTO target_policies (id, organization_id, version, allowlist) "
                "VALUES (:id, :org, 1, :allowlist) "
                "ON CONFLICT (organization_id, version) DO NOTHING"
            ),
            {
                "id": uuid.uuid4(),
                "org": org_id,
                "allowlist": sa.text("'[\"demo-target\"]'::jsonb"),
            },
        )
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
```

Note: binding a JSONB literal via `sa.text` inside a parameter does not work. Pass the allowlist as a JSON string and cast in SQL: `VALUES (:id, :org, 1, CAST(:allowlist AS jsonb))` with `{"allowlist": '["demo-target"]'}`.

- [ ] **Step 4: Wire the seed into `make dev`**

```makefile
dev:
	$(COMPOSE) up --build -d
	$(COMPOSE) exec -T api uv run alembic -c apps/api/alembic.ini upgrade head
	$(COMPOSE) exec -T api uv run python -m plimsoll_api.seed
	@echo "Plimsoll is up. API http://localhost:8000  demo target http://localhost:8080"
	@echo "Sign in with admin@demo.plimsoll.dev / plimsoll-demo-password"
```

- [ ] **Step 5: Run the tests and make sure they pass**

Run: `make dev-down && make dev && make test-int`
Expected: PASS — five seed tests, and Task 14's auth tests now pass too.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -s -m "feat(api): idempotent demo seed with an allowlisted demo target"
```

---

### Task 16: TypeScript contract generation

**Files:**
- Create: `package.json`, `pnpm-workspace.yaml`, `packages/contracts/typescript/package.json`
- Create: `packages/contracts/typescript/src/generated.ts` (generated, committed)
- Modify: `Makefile`
- Test: `apps/api/tests/integration/test_contracts_generation.py`

**Interfaces:**
- Consumes: the running API (Task 7), the auth contracts (Task 14).
- Produces: `make contracts`, which regenerates `generated.ts` and must leave the working tree clean.

- [ ] **Step 1: Write the failing test**

`apps/api/tests/integration/test_contracts_generation.py`:

```python
import pathlib
import subprocess

import pytest

pytestmark = pytest.mark.integration

GENERATED = pathlib.Path("packages/contracts/typescript/src/generated.ts")


def test_generated_types_are_committed() -> None:
    assert GENERATED.exists(), "run `make contracts`"
    assert "TokenResponse" in GENERATED.read_text()


def test_regeneration_leaves_the_tree_clean() -> None:
    subprocess.run(["make", "contracts"], check=True)
    changed = subprocess.run(
        ["git", "diff", "--name-only", "--", str(GENERATED)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert changed == "", "generated types are stale; commit the regenerated file"
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest apps/api/tests/integration/test_contracts_generation.py -v -m integration`
Expected: FAIL — the generated file does not exist.

- [ ] **Step 3: Create the pnpm workspace**

`pnpm-workspace.yaml`:

```yaml
packages:
  - "packages/*/typescript"
  - "apps/web"
```

Root `package.json`:

```json
{
  "name": "plimsoll",
  "private": true,
  "packageManager": "pnpm@9.7.0"
}
```

`packages/contracts/typescript/package.json`:

```json
{
  "name": "@plimsoll/contracts",
  "version": "0.1.0",
  "private": true,
  "main": "src/generated.ts",
  "types": "src/generated.ts",
  "devDependencies": {
    "openapi-typescript": "^7.3.0",
    "typescript": "^5.5.0"
  }
}
```

- [ ] **Step 4: Add the `contracts` target**

```makefile
.PHONY: contracts

contracts:
	$(COMPOSE) exec -T api uv run python -c \
	  "import json, plimsoll_api.main as m; print(json.dumps(m.app.openapi()))" \
	  > /tmp/plimsoll-openapi.json
	docker run --rm -v "$$PWD:/w" -v /tmp:/tmp -w /w node:20-alpine sh -c \
	  "npx --yes openapi-typescript@7 /tmp/plimsoll-openapi.json \
	   -o packages/contracts/typescript/src/generated.ts"
```

Generation runs in a container so the Docker-and-make-only promise holds.

- [ ] **Step 5: Generate, inspect, and commit the output**

Run: `make dev && make contracts`
Expected: `packages/contracts/typescript/src/generated.ts` exists and contains `TokenResponse` and `MeResponse`. Read the first 30 lines to confirm it is a real schema document, not an error page.

- [ ] **Step 6: Run the tests and make sure they pass**

Run: `make test-int`
Expected: PASS — both generation tests.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -s -m "feat(contracts): generate TypeScript types from the OpenAPI document"
```

---

## Slice acceptance

Run every gate from a clean clone before declaring S1 done:

```bash
make dev-down
make dev
make lint
make typecheck
make test
make test-int
make contracts && git diff --quiet
```

Then confirm each acceptance criterion in the design:

- [ ] `make dev` succeeds with only Docker and make; `make dev-down` removes everything
- [ ] `/healthz`, `/readyz`, `/api/v1/version` behave, and `/readyz` returns 503 with a dependency stopped (`docker compose stop redis`, check, then `start`)
- [ ] The seeded admin can log in, refresh, call `/auth/me`, and log out
- [ ] Reusing a consumed refresh token revokes the family
- [ ] The tenant isolation test and its no-setting inverse pass, and Task 10 Step 5 proved they can fail
- [ ] Migrations apply and roll back cleanly
- [ ] `make contracts` leaves the tree clean

## Self-review notes

Checked against the design document:

- **Spec coverage.** Every design section maps to a task: layout and tooling (1), error spine (2, 4), config and logging (3), health (5), demo target (6), compose and roles (7), Alembic and extension (8), schema (9), RLS and the `SECURITY DEFINER` function (10), request-scoped session (11), passwords and access tokens (12), refresh rotation (13), endpoints (14), seed (15), contract generation (16).
- **One addition beyond the design.** Task 13 introduces `refresh_token_history`, because family revocation on *reuse* requires knowing that a presented token was previously valid, which the single `current_hash` column cannot answer. It is added in its own migration with the same RLS treatment. Update the design document's table count from twenty-three to twenty-four when this lands.
- **Known rough edges left for the implementer to resolve, flagged in place rather than hidden.** Task 5 Step 3 shows a wrong first attempt at injecting the app into a route and immediately corrects it; Task 13's fixture note and Task 14 Step 6's session-scoping note both mark places where the naive version reads the `users` table with no organisation set and must be restructured. These are called out because a fresh implementer would otherwise hit them at runtime with a confusing empty result rather than an error.
