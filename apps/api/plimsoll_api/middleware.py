from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

from plimsoll_api.logging import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_SCOPE_KEY = "plimsoll_request_id"


async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get(REQUEST_ID_HEADER) or f"req-{uuid.uuid4().hex[:12]}"
    # Also on the scope: error handlers that run outside this middleware cannot
    # read the context variable, because it has been reset by then.
    request.scope[REQUEST_ID_SCOPE_KEY] = request_id
    token = request_id_var.set(request_id)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response
