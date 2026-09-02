"""The aggregate root: who ordered, current state, chosen warehouse, money, and a
frozen copy of where it ships.

The shipping address is snapshotted rather than referenced. Editing an address later
must never change where a past order was sent — the same argument as unit_price on
order_items.
"""

import uuid
from typing import TYPE_CHECKING

from geoalchemy2 import Geography, WKBElement
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import ORDER_STATUS, OrderStatus

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.order_item import OrderItem
    from app.models.order_status_history import OrderStatusHistory
    from app.models.payment import Payment
    from app.models.reservation import StockReservation
    from app.models.warehouse import Warehouse


class Order(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "orders"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[OrderStatus] = mapped_column(
        ORDER_STATUS, nullable=False, server_default=text("'pending'")
    )
    # Assigned at reservation time, hence nullable while pending.
    warehouse_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("warehouses.id", ondelete="RESTRICT"),
        nullable=True,
    )

    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    total_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)

    shipping_line1: Mapped[str] = mapped_column(String, nullable=False)
    shipping_line2: Mapped[str | None] = mapped_column(String, nullable=True)
    shipping_city: Mapped[str] = mapped_column(String, nullable=False)
    shipping_postal_code: Mapped[str] = mapped_column(String, nullable=False)
    shipping_country_code: Mapped[str] = mapped_column(String(2), nullable=False)

    # No GiST index here: this is the *query* point that warehouse selection
    # orders by, not indexed data. The index that matters is warehouses.location.
    shipping_point: Mapped[WKBElement | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=True,
    )

    customer: Mapped["Customer"] = relationship(
        back_populates="orders", lazy="raise"
    )
    warehouse: Mapped["Warehouse | None"] = relationship(lazy="raise")
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", lazy="raise"
    )
    payments: Mapped[list["Payment"]] = relationship(
        back_populates="order", lazy="raise"
    )
    reservations: Mapped[list["StockReservation"]] = relationship(
        back_populates="order", lazy="raise"
    )
    status_history: Mapped[list["OrderStatusHistory"]] = relationship(
        back_populates="order", lazy="raise"
    )

    __table_args__ = (
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
        CheckConstraint(
            "shipping_country_code ~ '^[A-Z]{2}$'", name="shipping_country_format"
        ),
        CheckConstraint("total_minor >= 0", name="total_non_negative"),
        # Any state at or past 'reserved' must name both the warehouse the stock
        # came from and the geocoded point that chose it. payment_failed is on the
        # required side because it is only reachable after reservation.
        CheckConstraint(
            "status IN ('pending', 'cancelled') "
            "OR (warehouse_id IS NOT NULL AND shipping_point IS NOT NULL)",
            name="reserved_requires_warehouse_and_point",
        ),
        Index("ix_orders_customer_created", "customer_id", text("created_at DESC")),
        Index(
            "ix_orders_open",
            "status",
            "created_at",
            postgresql_where=text("status IN ('pending', 'reserved')"),
        ),
    )
