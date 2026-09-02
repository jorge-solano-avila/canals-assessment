"""Identity of the person an order belongs to.

Seed-only in this project: the brief rules out customer management APIs, so the
request body carries a customer_id and an unknown one is a 404.
"""

from typing import TYPE_CHECKING

from sqlalchemy import Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.address import Address
    from app.models.order import Order


class Customer(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "customers"

    email: Mapped[str] = mapped_column(String, nullable=False)
    full_name: Mapped[str] = mapped_column(String, nullable=False)

    addresses: Mapped[list["Address"]] = relationship(
        back_populates="customer", lazy="raise"
    )
    orders: Mapped[list["Order"]] = relationship(
        back_populates="customer", lazy="raise"
    )

    __table_args__ = (
        # Case-insensitive uniqueness without pulling in the citext extension.
        # Lookups must also use lower(email) to hit this index.
        Index("ix_customers_email_lower", text("lower(email)"), unique=True),
    )
