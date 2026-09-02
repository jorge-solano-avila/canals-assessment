"""Pydantic v2 wire schemas.

Imports app.domain only. Nothing here knows SQLAlchemy exists, and nothing in
app/models imports from this package.
"""

from app.schemas.common import ErrorCode, ErrorResponse, FieldError
from app.schemas.order import (
    CreateOrderRequest,
    OrderItemIn,
    OrderItemOut,
    OrderResponse,
    ShippingAddressIn,
    WarehouseOut,
)

__all__ = [
    "CreateOrderRequest",
    "ErrorCode",
    "ErrorResponse",
    "FieldError",
    "OrderItemIn",
    "OrderItemOut",
    "OrderResponse",
    "ShippingAddressIn",
    "WarehouseOut",
]
