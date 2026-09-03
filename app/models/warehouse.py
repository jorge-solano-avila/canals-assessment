"""Physical fulfillment locations — the candidate set ranked by PostGIS distance.

location is NOT NULL because a warehouse without coordinates can never win that
ranking, so a row without one would be a silent hole in fulfillment.
"""

from geoalchemy2 import Geography, WKBElement
from sqlalchemy import Boolean, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Warehouse(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "warehouses"

    code: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)

    # spatial_index=False -> the GiST index is written explicitly in the migration.
    # WKBElement is what a read returns; writes go through app.db.geo.to_point.
    location: Mapped[WKBElement] = mapped_column(
        Geography(geometry_type="POINT", srid=4326, spatial_index=False),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    __table_args__ = (
        # Declared here as well as in the migration: the metadata must match the
        # database, or autogenerate proposes dropping it.
        Index("ix_warehouses_location", "location", postgresql_using="gist"),
    )
