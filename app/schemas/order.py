"""Request and response schemas for POST /orders.

This module imports only from app.domain — never from app.models. The ORM and the
wire format are separate concerns and neither is allowed to leak into the other.
"""

from datetime import datetime
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from app.domain.enums import OrderStatus

# --------------------------------------------------------------------------- in


class ShippingAddressIn(BaseModel):
    """Structured, never a single free-text blob.

    Geocoding a blob is guesswork; geocoding fields is a lookup. The field sizes
    and the country-code shape mirror the database CHECK constraints so a request
    that would violate them fails at the edge with a 422 rather than deep in a
    transaction with a 500.
    """

    model_config = ConfigDict(extra="forbid")

    line1: str = Field(min_length=1, max_length=200)
    line2: str | None = Field(default=None, max_length=200)
    city: str = Field(min_length=1, max_length=120)
    postal_code: str = Field(min_length=1, max_length=20)
    country_code: str = Field(
        min_length=2,
        max_length=2,
        pattern=r"^[A-Za-z]{2}$",
        description="ISO 3166-1 alpha-2; normalised to upper case.",
    )

    @field_validator("country_code")
    @classmethod
    def _upper(cls, v: str) -> str:
        # The DB CHECK is ^[A-Z]{2}$, so normalise rather than reject 'es'.
        return v.upper()


class OrderItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: UUID
    quantity: int = Field(gt=0, description="Must be positive; matches the DB CHECK.")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


class CreateOrderRequest(BaseModel):
    """Body of POST /orders.

    Two things are deliberately absent. `currency` is derived from the products —
    a client that could name the currency could name the price. The idempotency
    key is an HTTP header (`Idempotency-Key`), not a body field, because it
    identifies the request rather than describing the order.

    hide_input_in_errors is the load-bearing setting here: without it, Pydantic
    attaches the offending input to every validation error and FastAPI renders
    those into the 422 body — so a request that fails validation on *any* field
    can echo the submitted card number back to the caller and into the logs.
    """

    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    customer_id: UUID
    shipping_address: ShippingAddressIn
    items: list[OrderItemIn] = Field(min_length=1)

    card_number: SecretStr = Field(
        exclude=True,
        description="Never stored, never logged; only brand and last four survive.",
    )

    @field_validator("card_number")
    @classmethod
    def _validate_card(cls, v: SecretStr) -> SecretStr:
        # The raw value is read here and nowhere else in this module. It is not
        # interpolated into any message: every failure below is a constant
        # string, so the PAN cannot reach a client or a log via an exception.
        raw = v.get_secret_value()
        digits = raw.replace(" ", "").replace("-", "")
        if not digits.isdigit():
            raise ValueError("card number must contain only digits, spaces or hyphens")
        if not 12 <= len(digits) <= 19:
            raise ValueError("card number must be between 12 and 19 digits")
        if not _luhn_ok(digits):
            raise ValueError("card number failed checksum validation")
        return v

    @model_validator(mode="after")
    def _no_duplicate_products(self) -> "CreateOrderRequest":
        seen: set[UUID] = set()
        for item in self.items:
            if item.product_id in seen:
                raise ValueError(f"duplicate product_id in items: {item.product_id}")
            seen.add(item.product_id)
        return self


# -------------------------------------------------------------------------- out


class WarehouseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str


class OrderItemOut(BaseModel):
    """Read entirely from the order_items snapshot columns.

    product_sku, product_name, unit_price_minor and currency are the values
    captured at purchase time, never a join back to `products`. A price change
    tomorrow must not rewrite what this order cost.
    """

    model_config = ConfigDict(from_attributes=True)

    product_id: UUID
    product_sku: str
    product_name: str
    quantity: int
    unit_price_minor: int
    currency: str
    line_total_minor: int


class OrderResponse(BaseModel):
    """The 201 body for a created order.

    Requires the ORM object to have been loaded with selectinload(Order.items);
    every relationship is lazy="raise", so building this from a bare Order raises
    instead of silently issuing N+1 queries. That is the setting working.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: OrderStatus
    warehouse: WarehouseOut | None = Field(
        default=None, description="Null while the order is pending or cancelled."
    )
    items: list[OrderItemOut]
    total_minor: int = Field(description="Integer minor units, e.g. cents.")
    currency: str
    payment_reference: str | None = Field(
        default=None,
        description="Provider reference for the successful charge, if any.",
    )
    created_at: datetime
