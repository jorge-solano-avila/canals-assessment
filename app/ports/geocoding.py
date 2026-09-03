"""The geocoding port.

Services depend on this Protocol; adapters implement it. Nothing here knows
about HTTP, Redis, or any particular provider.
"""

from typing import Protocol

from app.domain.address import PostalAddress
from app.domain.geo import Coordinates


class GeocodingProvider(Protocol):
    async def geocode(self, address: PostalAddress) -> Coordinates:
        """Resolve an address to coordinates.

        Raises:
            GeocodingFailed: the address could not be resolved.
        """
        ...
