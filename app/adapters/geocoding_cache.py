"""Redis caching decorator for any GeocodingProvider.

REDIS IS NEVER ON THE CRITICAL PATH. Every call into Redis is wrapped: on any
RedisError or timeout the adapter logs once and falls through to the wrapped
provider. A read failure skips the cache, a write failure is ignored, and no
exception originating in Redis escapes this module. A request must never fail
because a cache is unavailable.

Written as a decorator rather than folded into MockGeocoder so the caching
behavior is testable on its own and the mock stays cache-unaware.
"""

import hashlib
import json
import logging

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.domain.address import PostalAddress
from app.domain.geo import Coordinates
from app.ports.geocoding import GeocodingProvider

logger = logging.getLogger(__name__)

# Bumping this prefix invalidates every entry at once. Needed whenever the
# normalization rule changes, since old keys would otherwise map stale points.
_KEY_PREFIX = "geocode:v1:"


class CachedGeocoder:
    """Implements GeocodingProvider by wrapping another one."""

    def __init__(
        self,
        inner: GeocodingProvider,
        redis: Redis,
        ttl_seconds: int,
    ) -> None:
        self._inner = inner
        self._redis = redis
        self._ttl = ttl_seconds

    async def geocode(self, address: PostalAddress) -> Coordinates:
        key = self._key(address)

        cached = await self._get(key)
        if cached is not None:
            return cached

        # A miss, or an unreachable cache, both land here.
        coordinates = await self._inner.geocode(address)
        await self._set(key, coordinates)
        return coordinates

    @staticmethod
    def _key(address: PostalAddress) -> str:
        digest = hashlib.sha256(address.normalized_key().encode("utf-8")).hexdigest()
        return f"{_KEY_PREFIX}{digest}"

    async def _get(self, key: str) -> Coordinates | None:
        try:
            raw = await self._redis.get(key)
        except (RedisError, OSError, TimeoutError) as exc:
            logger.warning("geocoding cache unavailable on read: %s", exc)
            return None

        if raw is None:
            return None

        try:
            payload = json.loads(raw)
            return Coordinates(lat=float(payload["lat"]), lon=float(payload["lon"]))
        except (ValueError, TypeError, KeyError) as exc:
            # A corrupt or foreign value is not worth failing a request over.
            logger.warning("discarding unreadable geocoding cache entry: %s", exc)
            return None

    async def _set(self, key: str, coordinates: Coordinates) -> None:
        payload = json.dumps({"lat": coordinates.lat, "lon": coordinates.lon})
        try:
            await self._redis.set(key, payload, ex=self._ttl)
        except (RedisError, OSError, TimeoutError) as exc:
            logger.warning("geocoding cache unavailable on write: %s", exc)
