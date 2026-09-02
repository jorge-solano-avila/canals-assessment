"""Audit trail of the order state machine — one row per transition.

orders.status says where an order *is*; this says how it got there: how long the
charge took, whether a cancellation came from a person or the expiry sweeper, how
often payments fail at a given step.

This is an audit log, NOT event sourcing. orders.status remains the single source of
truth and is never reconstructed by replaying this table; the history is derived,
append-only, and safe to truncate without affecting correctness.

Rows are written by the application inside the same transaction as the status change
itself, so the history can never disagree with orders.status.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import ORDER_STATUS, OrderStatus

if TYPE_CHECKING:
    from app.models.order import Order


class OrderStatusHistory(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "order_status_history"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    # NULL on the row recording creation, where there is no previous state.
    from_status: Mapped[Optional[OrderStatus]] = mapped_column(
        ORDER_STATUS, nullable=True
    )
    to_status: Mapped[OrderStatus] = mapped_column(ORDER_STATUS, nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    order: Mapped["Order"] = relationship(
        back_populates="status_history", lazy="raise"
    )

    __table_args__ = (
        # IS DISTINCT FROM rather than <> so the NULL creation row passes.
        CheckConstraint(
            "from_status IS DISTINCT FROM to_status", name="transition_changes_status"
        ),
        # The per-order timeline is the only way this table is ever read.
        Index("ix_order_status_history_order", "order_id", "occurred_at"),
    )
