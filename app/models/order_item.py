"""Order lines, with price, currency, SKU and name frozen at purchase time.

The snapshot is the point: rendering a past order must never join back to products
and silently pick up today's price or a renamed SKU.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Computed,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAtMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.order import Order
    from app.models.product import Product


class OrderItem(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "order_items"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # --- snapshots, never re-derived by joining back to products ---
    product_sku: Mapped[str] = mapped_column(String, nullable=False)
    product_name: Mapped[str] = mapped_column(String, nullable=False)
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    # Generated rather than computed in the app, so it cannot drift.
    line_total_minor: Mapped[int] = mapped_column(
        BigInteger,
        Computed("quantity * unit_price_minor", persisted=True),
        nullable=False,
    )

    order: Mapped["Order"] = relationship(back_populates="items", lazy="raise")
    product: Mapped["Product"] = relationship(lazy="raise")

    __table_args__ = (
        # One line per product. This is also what makes
        # HAVING count(DISTINCT product_id) = n_items a correct completeness test.
        UniqueConstraint(
            "order_id", "product_id", name="uq_order_items_order_product"
        ),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price_minor >= 0", name="unit_price_non_negative"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
    )
