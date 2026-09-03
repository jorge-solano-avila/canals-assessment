"""Geocoding determinism and cache degradation.

These two properties are the reason the mock and the cache exist at all: a
reviewer re-running the suite must get identical coordinates, and a dead Redis
must not fail a request.
"""

import logging

import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.geocoding_cache import CachedGeocoder
from app.adapters.geocoding_mock import MockGeocoder
from app.domain.address import PostalAddress
from app.domain.errors import GeocodingFailed
from app.domain.geo import Coordinates
from app.services.warehouse_selection import WarehouseSelectionService
from tests.conftest import items, make_product, make_stock, make_warehouse

KNOWN = PostalAddress(
    line1="Calle de Alcala 45", city="Madrid", postal_code="28014", country_code="ES"
)
UNKNOWN = PostalAddress(
    line1="17 Nowhere Lane", city="Fictionville", postal_code="ZZ999", country_code="ES"
)


async def test_known_city_resolves_to_real_coordinates() -> None:
    point = await MockGeocoder().geocode(KNOWN)
    assert (round(point.lat, 3), round(point.lon, 3)) == (40.417, -3.704)


async def test_unknown_address_is_deterministic_within_a_process() -> None:
    """Repeated calls must agree; the fallback is a hash, not a random point."""
    geocoder = MockGeocoder()
    first = await geocoder.geocode(UNKNOWN)
    others = [await geocoder.geocode(UNKNOWN) for _ in range(5)]
    assert all(o == first for o in others)


def test_unknown_address_is_deterministic_across_processes() -> None:
    """The property that actually matters, and the one an in-process assertion
    cannot check.

    Python's hash() is salted per process by PYTHONHASHSEED, so a fallback built
    on it would pass the test above and still return different coordinates on
    every run. Two subprocesses with different seeds prove sha256 is used.
    """
    import json
    import subprocess

    script = (
        "import asyncio, json;"
        "from app.adapters.geocoding_mock import MockGeocoder;"
        "from app.domain.address import PostalAddress;"
        "a=PostalAddress(line1='17 Nowhere Lane',city='Fictionville',"
        "postal_code='ZZ999',country_code='ES');"
        "c=asyncio.run(MockGeocoder().geocode(a));"
        "print(json.dumps([c.lat, c.lon]))"
    )
    outputs = []
    for seed in ("0", "1", "12345"):
        result = subprocess.run(
            ["python", "-c", script],
            capture_output=True,
            text=True,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "PYTHONHASHSEED": seed},
            cwd="/app",
        )
        assert result.returncode == 0, result.stderr
        outputs.append(json.loads(result.stdout))

    assert outputs[0] == outputs[1] == outputs[2]


async def test_empty_city_raises_geocoding_failed() -> None:
    with pytest.raises(GeocodingFailed):
        await MockGeocoder().geocode(
            PostalAddress(line1="x", city="   ", postal_code="1", country_code="ES")
        )


async def test_selection_succeeds_when_redis_is_unavailable(
    session: AsyncSession,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A dead cache degrades to uncached geocoding; it never fails the request."""
    product = await make_product(session, "SKU-KEYB")
    warehouse = await make_warehouse(session, "MAD-01", at=Coordinates(lat=40.4168, lon=-3.7038))
    await make_stock(session, warehouse, product, on_hand=10)
    await session.commit()

    # Port 1 is closed; every Redis call will fail.
    dead = Redis.from_url(
        "redis://127.0.0.1:1/0", socket_connect_timeout=0.1, socket_timeout=0.1
    )
    geocoder = CachedGeocoder(inner=MockGeocoder(), redis=dead, ttl_seconds=60)

    svc = WarehouseSelectionService(session=session, geocoder=geocoder)
    with caplog.at_level(logging.WARNING):
        chosen = await svc.select(KNOWN, items((product.id, 1)))

    assert chosen.code == "MAD-01"
    assert any("cache unavailable" in r.message for r in caplog.records)
    await dead.aclose()


async def test_cache_returns_stored_value_and_survives_corruption() -> None:
    """A hit is served from Redis; unreadable content falls through, not fails."""

    class _FakeRedis:
        def __init__(self) -> None:
            self.store: dict[str, str] = {}

        async def get(self, key: str) -> str | None:
            return self.store.get(key)

        async def set(self, key: str, value: str, ex: int | None = None) -> None:
            self.store[key] = value

    fake = _FakeRedis()
    geocoder = CachedGeocoder(inner=MockGeocoder(), redis=fake, ttl_seconds=60)  # type: ignore[arg-type]

    first = await geocoder.geocode(KNOWN)
    assert len(fake.store) == 1
    assert await geocoder.geocode(KNOWN) == first  # served from cache

    # Corrupt the entry: the adapter must fall through, not raise.
    key = next(iter(fake.store))
    fake.store[key] = "not json"
    assert await geocoder.geocode(KNOWN) == first


def test_line2_is_excluded_from_the_cache_key() -> None:
    """An apartment number does not move the building."""
    base = PostalAddress(
        line1="Calle de Alcala 45", city="Madrid", postal_code="28014", country_code="ES"
    )
    with_flat = PostalAddress(
        line1="Calle de Alcala 45",
        city="Madrid",
        postal_code="28014",
        country_code="ES",
        line2="4B",
    )
    assert base.normalized_key() == with_flat.normalized_key()


def test_normalization_collapses_case_and_whitespace() -> None:
    a = PostalAddress(
        line1="  Calle   de Alcala 45 ", city="MADRID", postal_code="28014",
        country_code="es",
    )
    b = PostalAddress(
        line1="calle de alcala 45", city="madrid", postal_code="28014",
        country_code="ES",
    )
    assert a.normalized_key() == b.normalized_key()
