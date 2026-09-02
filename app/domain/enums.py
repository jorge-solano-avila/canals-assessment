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
