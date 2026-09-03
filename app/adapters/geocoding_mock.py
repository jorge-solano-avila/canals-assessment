"""A deterministic mock geocoder.

DETERMINISM IS THE POINT. The same address must always yield the same point,
across calls, across processes and across machines — reviewers re-run this.

That rules out Python's built-in hash(): it is salted per process by
PYTHONHASHSEED, so a fallback built on it returns different coordinates on every
interpreter start. hashlib.sha256 is stable by definition.
"""

import hashlib

from app.domain.address import PostalAddress
from app.domain.errors import GeocodingFailed
from app.domain.geo import Coordinates

# Real coordinates, keyed by (casefolded city, upper country code).
_CITIES: dict[tuple[str, str], Coordinates] = {
    ("madrid", "ES"): Coordinates(lat=40.4168, lon=-3.7038),
    ("barcelona", "ES"): Coordinates(lat=41.3851, lon=2.1734),
    ("valencia", "ES"): Coordinates(lat=39.4699, lon=-0.3763),
    ("sevilla", "ES"): Coordinates(lat=37.3891, lon=-5.9845),
    ("seville", "ES"): Coordinates(lat=37.3891, lon=-5.9845),
    ("bilbao", "ES"): Coordinates(lat=43.2630, lon=-2.9350),
    ("zaragoza", "ES"): Coordinates(lat=41.6488, lon=-0.8891),
    ("malaga", "ES"): Coordinates(lat=36.7213, lon=-4.4214),
    ("lisboa", "PT"): Coordinates(lat=38.7223, lon=-9.1393),
    ("lisbon", "PT"): Coordinates(lat=38.7223, lon=-9.1393),
    ("porto", "PT"): Coordinates(lat=41.1579, lon=-8.6291),
    ("paris", "FR"): Coordinates(lat=48.8566, lon=2.3522),
}

# Fallback points land inside the Iberian peninsula rather than mid-ocean, so an
# unknown address still produces an explicable selection in a demo.
_LAT_MIN, _LAT_MAX = 36.0, 43.8
_LON_MIN, _LON_MAX = -9.3, 3.3


class MockGeocoder:
    """Implements GeocodingProvider. No network, no state, no randomness."""

    async def geocode(self, address: PostalAddress) -> Coordinates:
        if not address.city.strip():
            raise GeocodingFailed("city is empty")

        key = (address.city.strip().casefold(), address.country_code.strip().upper())
        known = _CITIES.get(key)
        if known is not None:
            return known

        return self._derive(address)

    @staticmethod
    def _derive(address: PostalAddress) -> Coordinates:
        """Map the address hash into the Iberian bounding box.

        Two independent 4-byte slices of the digest give latitude and longitude,
        so neighboring addresses do not collapse onto a line.
        """
        digest = hashlib.sha256(address.normalized_key().encode("utf-8")).digest()
        lat_frac = int.from_bytes(digest[0:4], "big") / 0xFFFFFFFF
        lon_frac = int.from_bytes(digest[4:8], "big") / 0xFFFFFFFF
        return Coordinates(
            lat=round(_LAT_MIN + lat_frac * (_LAT_MAX - _LAT_MIN), 6),
            lon=round(_LON_MIN + lon_frac * (_LON_MAX - _LON_MIN), 6),
        )
