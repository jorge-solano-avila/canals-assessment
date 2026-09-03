"""Geographic value objects.

Distinct from app/db/geo.py, which builds the WKB values the database stores.
This module is pure data and has no SQLAlchemy or GeoAlchemy2 import, so ports
and services can speak coordinates without depending on persistence.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Coordinates:
    """A WGS84 point. Latitude and longitude are named, never positional pairs."""

    lat: float
    lon: float

    def __post_init__(self) -> None:
        if not -90.0 <= self.lat <= 90.0:
            raise ValueError(f"latitude out of range: {self.lat}")
        if not -180.0 <= self.lon <= 180.0:
            raise ValueError(f"longitude out of range: {self.lon}")
