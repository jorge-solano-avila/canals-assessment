"""Shared domain vocabulary.

This module is deliberately neutral: plain Python enums, no SQLAlchemy, no
Pydantic. Models and schemas both import from here, which is how they share a
vocabulary without importing each other's concerns.

The SQLAlchemy ENUM type instances that bind these to the PostgreSQL types live
in app/models/enums.py; nothing here knows the database exists.
"""

import enum


class OrderStatus(str, enum.Enum):
    """pending -> reserved -> paid -> confirmed, with two compensating paths."""

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


class IdempotencyStatus(str, enum.Enum):
    """Restored in migration 0003.

    The phase-2 derived model (response_status IS NULL) could express settled vs
    unsettled, but not "settled as a failure that may be retried" apart from
    "settled, replay this forever". `failed -> retry allowed` needs a stored state.
    """

    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"
