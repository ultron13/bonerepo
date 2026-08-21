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
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    CONFLICT = "CONFLICT"
    RESOURCE_IN_USE = "RESOURCE_IN_USE"
    IDEMPOTENCY_KEY_REUSED = "IDEMPOTENCY_KEY_REUSED"
    TEST_NOT_RUNNABLE = "TEST_NOT_RUNNABLE"
    TARGET_NOT_ALLOWED = "TARGET_NOT_ALLOWED"
    INSUFFICIENT_CAPACITY = "INSUFFICIENT_CAPACITY"
    REPO_UNREACHABLE = "REPO_UNREACHABLE"
    RATE_LIMITED = "RATE_LIMITED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    INTERNAL = "INTERNAL"


HTTP_STATUS_FOR_CODE: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_FAILED: 422,
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.PERMISSION_DENIED: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.METHOD_NOT_ALLOWED: 405,
    ErrorCode.CONFLICT: 409,
    ErrorCode.RESOURCE_IN_USE: 409,
    ErrorCode.IDEMPOTENCY_KEY_REUSED: 409,
    ErrorCode.TEST_NOT_RUNNABLE: 422,
    ErrorCode.TARGET_NOT_ALLOWED: 422,
    ErrorCode.INSUFFICIENT_CAPACITY: 422,
    ErrorCode.REPO_UNREACHABLE: 422,
    ErrorCode.RATE_LIMITED: 429,
    # 503, not 500. A dependency being down is not a fault in the request:
    # 500 tells a client the server has a bug, so pipelines do not retry it and
    # it pages whoever owns the code rather than whoever owns the database.
    ErrorCode.DEPENDENCY_UNAVAILABLE: 503,
    ErrorCode.INTERNAL: 500,
}


class ErrorBody(BaseModel):
    # serialize_by_alias so no caller can forget `by_alias=True` and emit
    # request_id where the contract says requestId.
    model_config = ConfigDict(populate_by_name=True, serialize_by_alias=True)

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
            error=ErrorBody(code=code, message=message, details=details, request_id=request_id)
        )
