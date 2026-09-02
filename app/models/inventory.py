"""The stock ledger: how many units exist and how many are already promised.

Availability is on_hand - reserved. This is the row the atomic conditional UPDATE
contends on:

    UPDATE inventory SET reserved = reserved + :qty
    WHERE warehouse_id = :w AND product_id = :p AND on_hand - reserved >= :qty
    RETURNING id

Never read-check-write: the WHERE clause and the write are the same statement, so
two concurrent orders cannot both win the last unit.
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
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.product import Product
    from app.models.warehouse import Warehouse


class Inventory(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "inventory"

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

    on_hand: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    reserved: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    warehouse: Mapped["Warehouse"] = relationship(lazy="raise")
    product: Mapped["Product"] = relationship(lazy="raise")

    __table_args__ = (
        UniqueConstraint(
            "warehouse_id", "product_id", name="uq_inventory_warehouse_product"
        ),
        CheckConstraint("reserved >= 0", name="reserved_non_negative"),
        CheckConstraint("on_hand >= reserved", name="on_hand_ge_reserved"),
        # The unique constraint's index leads with warehouse_id and cannot serve
        # warehouse selection, which filters by product_id IN (...) first.
        Index("ix_inventory_product_warehouse", "product_id", "warehouse_id"),
    )
