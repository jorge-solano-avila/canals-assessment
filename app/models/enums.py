"""SQLAlchemy bindings for the PostgreSQL enum types.

The enums themselves live in app/domain/enums.py so that schemas can share them
without importing anything from the model layer. This module holds only the
database binding.

create_type=False everywhere: the types were created once, explicitly, in
migration 0001 before any table existed. Letting SQLAlchemy create them
implicitly would make the ordering depend on which table is emitted first.
"""

import enum

from sqlalchemy.dialects.postgresql import ENUM

from app.domain.enums import (
    IdempotencyStatus,
    OrderStatus,
    PaymentStatus,
    ReservationStatus,
)

__all__ = [
    "IDEMPOTENCY_STATUS",
    "ORDER_STATUS",
    "PAYMENT_STATUS",
    "RESERVATION_STATUS",
    "IdempotencyStatus",
    "OrderStatus",
    "PaymentStatus",
    "ReservationStatus",
]


def _pg_enum(python_enum: type[enum.Enum], name: str) -> ENUM:
    return ENUM(
        python_enum,
        name=name,
        create_type=False,
        values_callable=lambda e: [member.value for member in e],
    )


ORDER_STATUS = _pg_enum(OrderStatus, "order_status")
PAYMENT_STATUS = _pg_enum(PaymentStatus, "payment_status")
RESERVATION_STATUS = _pg_enum(ReservationStatus, "reservation_status")
IDEMPOTENCY_STATUS = _pg_enum(IdempotencyStatus, "idempotency_status")
