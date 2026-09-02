"""Native PostgreSQL enum types.

create_type=False everywhere: the types are created once, explicitly, in migration
0001 before any table exists. Letting SQLAlchemy create them implicitly would make
the ordering depend on which table happens to be emitted first.
"""

import enum

from sqlalchemy.dialects.postgresql import ENUM


class OrderStatus(str, enum.Enum):
    pending = "pending"
    reserved = "reserved"
    paid = "paid"
    confirmed = "confirmed"
    payment_failed = "payment_failed"
    cancelled = "cancelled"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    succeeded = "succeeded"
    failed = "failed"


class ReservationStatus(str, enum.Enum):
    active = "active"
    committed = "committed"
    released = "released"
    expired = "expired"


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
