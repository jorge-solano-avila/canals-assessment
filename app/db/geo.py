"""Construction of geography POINT values.

Coordinates are `float`, not `Decimal`, on purpose. PostGIS stores geography
coordinates as IEEE 754 double precision: a 22-digit longitude round-trips as
-3.70381234567890123456 -> -3.703812345678901, so Decimal would promise an
exactness the column discards on write. (Money is the opposite case, which is why
every amount in this schema is an integer count of minor units.) Seven decimal
places is roughly a centimeter; float64 gives about thirteen at this magnitude.
"""

from typing import cast

from geoalchemy2 import WKBElement, WKTElement

SRID = 4326


def to_point(*, lon: float, lat: float) -> WKBElement:
    """Build a geography POINT for writing to the database.

    Keyword-only by design. Two positional floats of the same type are exactly
    the shape that lets latitude and longitude transpose silently, and a swapped
    pair is usually still a valid point somewhere on Earth — Madrid's
    (-3.7038, 40.4168) reversed lands in the Indian Ocean. Nothing downstream
    would object: not the column type, not the CHECK constraints, not the
    distance query. Warehouse selection would simply return the wrong warehouse.

    The return type is a deliberate, single lie: the column accepts WKT on write
    and yields WKB on read, and no one annotation is true for both. Confining the
    cast here keeps `Any` from spreading into every caller.
    """
    if not -180.0 <= lon <= 180.0:
        raise ValueError(f"longitude out of range: {lon}")
    if not -90.0 <= lat <= 90.0:
        raise ValueError(f"latitude out of range: {lat}")
    return cast(WKBElement, WKTElement(f"POINT({lon} {lat})", srid=SRID))
