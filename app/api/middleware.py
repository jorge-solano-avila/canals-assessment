"""Correlation id middleware."""

import uuid
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

from app.api.logging import correlation_id

HEADER = "X-Request-ID"


async def correlation_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Bind one id for the life of the request, and echo it back.

    An inbound X-Request-ID is honoured so a trace spans services; otherwise one
    is generated. Stored in a ContextVar so every log line in this request picks
    it up without being passed the id explicitly.
    """
    incoming = request.headers.get(HEADER)
    request_id = incoming or uuid.uuid4().hex
    token = correlation_id.set(request_id)
    try:
        response = await call_next(request)
    finally:
        correlation_id.reset(token)
    response.headers[HEADER] = request_id
    return response
