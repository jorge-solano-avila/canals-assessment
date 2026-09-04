"""Geocoding determinism.

The property that matters: a reviewer re-running the suite must get identical
coordinates, in this process and in any other.
"""


import pytest

from app.adapters.geocoding_mock import MockGeocoder
from app.domain.address import PostalAddress
from app.domain.errors import GeocodingFailed

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


def test_line2_is_excluded_from_the_normalized_key() -> None:
    """An apartment number does not move the building.

    The normalized key is what the geocoder hashes for its deterministic
    fallback, so two addresses differing only by floor resolve identically.
    """
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
