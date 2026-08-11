from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from plimsoll_api.logging import request_id_var
from plimsoll_api.middleware import REQUEST_ID_SCOPE_KEY
from plimsoll_contracts.errors import HTTP_STATUS_FOR_CODE, ErrorCode, ErrorEnvelope

logger = logging.getLogger(__name__)

_STATUS_TO_CODE = {
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.PERMISSION_DENIED,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    422: ErrorCode.VALIDATION_FAILED,
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


def _request_id(request: Request) -> str:
    """Resolve the request ID for an envelope.

    The handler for an unhandled exception runs in ServerErrorMiddleware, which
    sits *outside* the request-ID middleware and therefore after the context
    variable has been reset. The scope carries the value across that boundary.
    """
    scoped = request.scope.get(REQUEST_ID_SCOPE_KEY)
    if isinstance(scoped, str) and scoped:
        return scoped
    return request_id_var.get()


def _envelope(
    request: Request, code: ErrorCode, message: str, details: dict[str, Any] | None
) -> JSONResponse:
    envelope = ErrorEnvelope.of(code, message, _request_id(request), details)
    return JSONResponse(
        status_code=HTTP_STATUS_FOR_CODE[code],
        content=envelope.model_dump(mode="json", exclude_none=True),
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(PlimsollError)
    async def _plimsoll(request: Request, exc: PlimsollError) -> JSONResponse:
        return _envelope(request, exc.code, exc.message, exc.details)

    @app.exception_handler(HTTPException)
    async def _http(request: Request, exc: HTTPException) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL)
        return _envelope(request, code, str(exc.detail), None)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [
            {"field": ".".join(str(p) for p in error["loc"][1:]), "problem": error["msg"]}
            for error in exc.errors()
        ]
        return _envelope(
            request,
            ErrorCode.VALIDATION_FAILED,
            "The request could not be accepted. See details for every problem found.",
            {"fields": fields},
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception")
        return _envelope(
            request,
            ErrorCode.INTERNAL,
            "An unexpected error occurred. Quote the request ID when reporting it.",
            None,
        )
