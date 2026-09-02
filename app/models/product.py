"""The sellable catalogue: what can be ordered and what it costs *today*.

It never answers "what did this order charge" — that is order_items. The separation
is what stops a price change from rewriting history.
"""

from sqlalchemy import BigInteger, Boolean, CheckConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Product(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "products"

    # Natural key the seeds upsert on, so re-seeding is stable across runs.
    sku: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)

    # Money as integer minor units, never float. BigInteger rather than Integer
    # removes any question of overflow in low-denomination currencies.
    unit_price_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    __table_args__ = (
        CheckConstraint("unit_price_minor >= 0", name="unit_price_non_negative"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
    )
