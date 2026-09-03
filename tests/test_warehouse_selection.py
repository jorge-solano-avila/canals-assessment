"""Warehouse selection semantics.

Targeted, not exhaustive. Each test pins one rule that would be expensive to get
wrong. Nothing here tests SQLAlchemy's mapping or Pydantic's validation.
"""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.geocoding_mock import MockGeocoder
from app.domain.address import PostalAddress
from app.domain.geo import Coordinates
from app.domain.errors import NoEligibleWarehouse, UnknownProduct
from app.services.warehouse_selection import WarehouseSelectionService
from tests.conftest import items, make_product, make_stock, make_warehouse

MADRID = PostalAddress(
    line1="Calle de Alcala 45", city="Madrid", postal_code="28014", country_code="ES"
)

# Distances from Madrid: Barcelona ~505 km, Seville ~390 km.
BARCELONA = Coordinates(lat=41.3851, lon=2.1734)
SEVILLE = Coordinates(lat=37.3891, lon=-5.9845)
MADRID_PT = Coordinates(lat=40.4168, lon=-3.7038)


def service(session: AsyncSession) -> WarehouseSelectionService:
    return WarehouseSelectionService(session=session, geocoder=MockGeocoder())


async def test_complete_far_warehouse_beats_incomplete_near_one(
    session: AsyncSession,
) -> None:
    """Completeness beats distance — the core semantic of this phase."""
    keyboard = await make_product(session, "SKU-KEYB")
    monitor = await make_product(session, "SKU-MON27")

    near = await make_warehouse(session, "MAD-01", at=MADRID_PT)
    far = await make_warehouse(session, "SVQ-01", at=SEVILLE)

    # The near warehouse is missing the monitor entirely.
    await make_stock(session, near, keyboard, on_hand=50)
    await make_stock(session, far, keyboard, on_hand=10)
    await make_stock(session, far, monitor, on_hand=5)
    await session.commit()

    chosen = await service(session).select(
        MADRID, items((keyboard.id, 1), (monitor.id, 1))
    )

    assert chosen.code == "SVQ-01"
    assert chosen.distance_m > 300_000  # genuinely the far one


async def test_insufficient_quantity_excludes_warehouse(
    session: AsyncSession,
) -> None:
    """It is a quantity test, not a presence test."""
    product = await make_product(session, "SKU-KEYB")
    near = await make_warehouse(session, "MAD-01", at=MADRID_PT)
    far = await make_warehouse(session, "SVQ-01", at=SEVILLE)

    await make_stock(session, near, product, on_hand=2)  # has it, but not enough
    await make_stock(session, far, product, on_hand=99)
    await session.commit()

    chosen = await service(session).select(MADRID, items((product.id, 10)))

    assert chosen.code == "SVQ-01"


async def test_availability_is_on_hand_minus_reserved(session: AsyncSession) -> None:
    """A warehouse whose stock is entirely reserved is excluded.

    on_hand alone looks healthy; only on_hand - reserved is availability.
    """
    product = await make_product(session, "SKU-KEYB")
    near = await make_warehouse(session, "MAD-01", at=MADRID_PT)
    far = await make_warehouse(session, "SVQ-01", at=SEVILLE)

    await make_stock(session, near, product, on_hand=100, reserved=100)
    await make_stock(session, far, product, on_hand=5)
    await session.commit()

    chosen = await service(session).select(MADRID, items((product.id, 1)))

    assert chosen.code == "SVQ-01"


async def test_equidistant_tie_broken_deterministically(
    session: AsyncSession,
) -> None:
    """Two warehouses at identical distance: the lower code wins, every time.

    Ordering on w.id would make this pass or fail by luck, since id is
    gen_random_uuid().
    """
    product = await make_product(session, "SKU-KEYB")
    # Same coordinates -> distance is exactly equal, not merely close.
    b = await make_warehouse(session, "BBB-01", at=MADRID_PT)
    a = await make_warehouse(session, "AAA-01", at=MADRID_PT)
    await make_stock(session, a, product, on_hand=10)
    await make_stock(session, b, product, on_hand=10)
    await session.commit()

    svc = service(session)
    picks = [(await svc.select(MADRID, items((product.id, 1)))).code for _ in range(5)]

    assert picks == ["AAA-01"] * 5


async def test_no_eligible_warehouse_raises_not_returns_none(
    session: AsyncSession,
) -> None:
    """The empty path is a domain error, never a None the caller can ignore."""
    keyboard = await make_product(session, "SKU-KEYB")
    monitor = await make_product(session, "SKU-MON27")

    # The two products exist but are split across warehouses.
    one = await make_warehouse(session, "MAD-01", at=MADRID_PT)
    two = await make_warehouse(session, "BCN-01", at=BARCELONA)
    await make_stock(session, one, keyboard, on_hand=10)
    await make_stock(session, two, monitor, on_hand=10)
    await session.commit()

    with pytest.raises(NoEligibleWarehouse) as exc:
        await service(session).select(MADRID, items((keyboard.id, 1), (monitor.id, 1)))

    # Both products are stocked somewhere; no single warehouse has both.
    assert exc.value.requested_products == 2
    assert exc.value.stocked_anywhere == 2


async def test_unknown_product_is_distinguishable_from_no_warehouse(
    session: AsyncSession,
) -> None:
    """A typo'd product id must not masquerade as 'out of stock'."""
    real = await make_product(session, "SKU-KEYB")
    warehouse = await make_warehouse(session, "MAD-01", at=MADRID_PT)
    await make_stock(session, warehouse, real, on_hand=10)
    await session.commit()

    from uuid import uuid4

    ghost = uuid4()
    with pytest.raises(UnknownProduct) as exc:
        await service(session).select(MADRID, items((real.id, 1), (ghost, 1)))

    assert exc.value.product_ids == frozenset({ghost})


async def test_inactive_warehouse_excluded(session: AsyncSession) -> None:
    """Nearest and fully stocked, but decommissioned."""
    product = await make_product(session, "SKU-KEYB")
    inactive = await make_warehouse(session, "MAD-01", at=MADRID_PT, is_active=False)
    active = await make_warehouse(session, "SVQ-01", at=SEVILLE)
    await make_stock(session, inactive, product, on_hand=999)
    await make_stock(session, active, product, on_hand=1)
    await session.commit()

    chosen = await service(session).select(MADRID, items((product.id, 1)))

    assert chosen.code == "SVQ-01"
