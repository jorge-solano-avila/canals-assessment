"""A customer's saved postal addresses with their geocoded coordinates.

Deliberately not on the POST /orders hot path — that request carries its address in
the body and snapshots it onto the order. This is the seeded address book.
"""

import uuid
from typing import TYPE_CHECKING

from geoalchemy2 import Geography, WKBElement
from sqlalchemy import CheckConstraint, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.customer import Customer


class Address(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "addresses"

    customer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="CASCADE"),
        nullable=False,
    )
    line1: Mapped[str] = mapped_column(String, nullable=False)
    line2: Mapped[str | None] = mapped_column(String, nullable=True)
    city: Mapped[str] = mapped_column(String, nullable=False)
    postal_code: Mapped[str] = mapped_column(String, nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)

    # Nullable: geocoding is an external call that can fail or lag, so NOT NULL
    # would block address creation on a third-party service.
    # spatial_index=False -> the GiST index is written explicitly in the migration.
    point: Mapped[WKBElement | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=True,
    )

    customer: Mapped["Customer"] = relationship(
        back_populates="addresses", lazy="raise"
    )

    __table_args__ = (
        CheckConstraint("country_code ~ '^[A-Z]{2}$'", name="country_code_format"),
        Index("ix_addresses_customer_id", "customer_id"),
        Index(
            "ix_addresses_point",
            "point",
            postgresql_using="gist",
            postgresql_where=text("point IS NOT NULL"),
        ),
    )
