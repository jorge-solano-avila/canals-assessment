"""Shared response shapes.

Every non-2xx response uses ErrorResponse, so a client has exactly one error
contract to handle. Served as `application/problem+json` by the phase-3 handlers.
"""

import enum

from pydantic import BaseModel, ConfigDict, Field


class ErrorCode(str, enum.Enum):
    """Machine-readable discriminator. Clients switch on this, not on prose."""

    validation_error = "validation_error"
    customer_not_found = "customer_not_found"
    product_not_found = "product_not_found"
    no_warehouse_available = "no_warehouse_available"
    insufficient_stock = "insufficient_stock"
    payment_declined = "payment_declined"
    payment_unresolved = "payment_unresolved"
    geocoding_failed = "geocoding_failed"
    idempotency_key_conflict = "idempotency_key_conflict"
    idempotency_in_progress = "idempotency_in_progress"
    internal_error = "internal_error"


class FieldError(BaseModel):
    """One field-level validation failure.

    There is deliberately no `input` field. Pydantic attaches the offending value
    to each validation error and FastAPI renders those into the 422 body — which
    is how a submitted card number ends up echoed back to the client and into the
    logs. CreateOrderRequest sets hide_input_in_errors=True to suppress it at the
    source; omitting `input` here means a handler cannot reintroduce it.
    """

    model_config = ConfigDict(extra="forbid")

    field: str = Field(description="Dotted path to the offending field.")
    message: str = Field(description="What was wrong with it.")


class ErrorResponse(BaseModel):
    """RFC 7807 Problem Details, plus one extension member.

    `code` is the extension: the spec's `type` URI is meant to identify the
    problem class, but comparing URIs is awkward for clients, and a flat enum is
    what people actually switch on. Extension members are spec-legal.
    """

    model_config = ConfigDict(extra="forbid")

    type: str = Field(
        default="about:blank",
        description="URI identifying the problem class.",
    )
    title: str = Field(description="Short, human-readable summary.")
    status: int = Field(description="HTTP status code, mirrored into the body.")
    detail: str | None = Field(
        default=None, description="Human-readable explanation of this occurrence."
    )
    instance: str | None = Field(
        default=None, description="Request path this occurred on."
    )
    code: ErrorCode = Field(description="Machine-readable error discriminator.")
    errors: list[FieldError] | None = Field(
        default=None, description="Field-level failures; validation errors only."
    )
