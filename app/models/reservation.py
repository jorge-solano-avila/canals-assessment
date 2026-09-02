"""The durable receipt for stock an order is holding, with an expiry.

inventory.reserved is only a counter — it says *how much* is held, never *by whom*.
This table says who holds what, so a failed payment can be compensated precisely and
the sweeper can return stock abandoned by a request that died mid-flight.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin
from app.models.enums import RESERVATION_STATUS, ReservationStatus

if TYPE_CHECKING:
    from app.models.order import Order


class StockReservation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "stock_reservations"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    warehouse_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=False,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ReservationStatus] = mapped_column(
        RESERVATION_STATUS, nullable=False, server_default=text("'active'")
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    order: Mapped["Order"] = relationship(
        back_populates="reservations", lazy="raise"
    )

    __table_args__ = (
        UniqueConstraint(
            "order_id",
            "warehouse_id",
            "product_id",
            name="uq_stock_reservations_order_warehouse_product",
        ),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint(
            "status = 'active' OR released_at IS NOT NULL",
            name="settled_requires_released_at",
        ),
        # Matches the sweeper's WHERE status = 'active' AND expires_at < now()
        # exactly. Stays small because settled reservations drop out of it.
        Index(
            "ix_stock_reservations_sweeper",
            "expires_at",
            postgresql_where=text("status = 'active'"),
        ),
    )
