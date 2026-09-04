"""Domain errors mapped to HTTP, in one consistent body shape.

EVERY non-2xx goes through here, including FastAPI's own validation errors, so a
client has exactly one error contract to parse. Served as
application/problem+json (RFC 7807) with a machine-readable `code` extension.

The service layer knows nothing about status codes; this is the only module that
does the translation.
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.domain.errors import (
    DomainError,
    GeocodingFailed,
    IdempotencyInProgress,
    IdempotencyKeyRequired,
    IdempotencyKeyReuse,
    InsufficientStock,
    InvalidStateTransition,
    MixedCurrencyBasket,
    NoEligibleWarehouse,
    PaymentDeclined,
    PaymentUnresolved,
    UnknownProduct,
)
from app.schemas.common import ErrorCode, ErrorResponse, FieldError

logger = logging.getLogger(__name__)

PROBLEM_JSON = "application/problem+json"

# exception -> (status, code, title)
_MAPPING: dict[type[DomainError], tuple[int, ErrorCode, str]] = {
    UnknownProduct: (422, ErrorCode.product_not_found, "Unknown product"),
    MixedCurrencyBasket: (422, ErrorCode.validation_error, "Mixed currencies"),
    IdempotencyKeyReuse: (422, ErrorCode.idempotency_key_conflict, "Idempotency key reuse"),
    IdempotencyKeyRequired: (400, ErrorCode.validation_error, "Idempotency key required"),
    NoEligibleWarehouse: (409, ErrorCode.no_warehouse_available, "No warehouse can fulfill this"),
    InsufficientStock: (409, ErrorCode.insufficient_stock, "Insufficient stock"),
    IdempotencyInProgress: (409, ErrorCode.idempotency_in_progress, "Request already in progress"),
    PaymentDeclined: (402, ErrorCode.payment_declined, "Payment declined"),
    PaymentUnresolved: (502, ErrorCode.payment_unresolved, "Payment outcome unknown"),
    GeocodingFailed: (502, ErrorCode.geocoding_failed, "Address could not be resolved"),
    InvalidStateTransition: (500, ErrorCode.internal_error, "Internal error"),
}


def _problem(
    *,
    status_code: int,
    code: ErrorCode,
    title: str,
    detail: str | None,
    instance: str,
    errors: list[FieldError] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        type=f"about:blank#{code.value}",
        title=title,
        status=status_code,
        detail=detail,
        instance=instance,
        code=code,
        errors=errors,
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        media_type=PROBLEM_JSON,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, DomainError)
        status_code, code, title = _MAPPING.get(
            type(exc), (500, ErrorCode.internal_error, "Internal error")
        )
        if status_code >= 500:
            # A 5xx is our fault or an upstream's; a 4xx is the client's and is
            # not worth an error-level line.
            logger.error("domain error: %s", exc, exc_info=exc)
        return _problem(
            status_code=status_code,
            code=code,
            title=title,
            # InvalidStateTransition leaks internal state names, so 500s get a
            # generic detail while the real one goes to the log above.
            detail=None if status_code >= 500 else str(exc),
            instance=request.url.path,
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, RequestValidationError)
        # `input` is deliberately NOT copied out of the Pydantic errors. The
        # request schema sets hide_input_in_errors=True, and FieldError has no
        # such field, so a submitted card number cannot be echoed back here.
        errors = [
            FieldError(
                field=".".join(str(p) for p in err["loc"][1:]) or "body",
                message=err["msg"],
            )
            for err in exc.errors()
        ]
        return _problem(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code=ErrorCode.validation_error,
            title="Request validation failed",
            detail="The request body did not pass validation.",
            instance=request.url.path,
            errors=errors,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception")
        return _problem(
            status_code=500,
            code=ErrorCode.internal_error,
            title="Internal error",
            detail=None,
            instance=request.url.path,
        )
