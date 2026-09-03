"""Reservation atomicity, idempotency and the sweeper.

These run against real PostgreSQL with genuinely separate connections. The
claims under test — "exactly one of N wins the last unit", "no deadlock", "a
second release is a no-op" — are properties of the engine's locking and
READ COMMITTED re-check, so nothing weaker than a real database can verify them.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.errors import InsufficientStock
from app.domain.geo import Coordinates
from app.domain.selection import RequestedItem
from app.models import Inventory, Order, StockReservation
from app.repositories.reservation import ReservationRepository
from tests.conftest import make_customer, make_product, make_stock, make_warehouse

MADRID = Coordinates(lat=40.4168, lon=-3.7038)


async def _bare_order(session: AsyncSession, warehouse_id: UUID) -> Order:
    """An order row to hang reservations off; the FK requires it to exist."""
    customer = await make_customer(session, email=f"c-{uuid4().hex[:8]}@example.com")
    order = Order(
        customer_id=customer.id,
        warehouse_id=warehouse_id,
        currency="EUR",
        total_minor=0,
        shipping_line1="x",
        shipping_city="Madrid",
        shipping_postal_code="28014",
        shipping_country_code="ES",
    )
    session.add(order)
    await session.flush()
    return order


async def test_n_coroutines_race_for_the_last_unit(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Exactly one of ten wins; the invariant holds afterwards.

    This is the core claim of the whole phase.
    """
    warehouse = await make_warehouse(session, "MAD-01", at=MADRID)
    product = await make_product(session, "SKU-KEYB")
    await make_stock(session, warehouse, product, on_hand=1)  # ONE unit
    customer = await make_customer(session)
    await session.commit()

    n = 10
    backend_pids: list[int] = []

    async def attempt() -> str:
        async with session_factory() as s:
            try:
                async with s.begin():
                    # Inside the transaction: a bare SELECT out here would
                    # autobegin one and make s.begin() raise. Recorded so a
                    # fixture regression that serialised these onto a single
                    # connection fails loudly instead of passing vacuously.
                    backend_pids.append(
                        await s.scalar(text("SELECT pg_backend_pid()"))
                    )
                    order = Order(
                        customer_id=customer.id,
                        warehouse_id=warehouse.id,
                        currency="EUR",
                        total_minor=0,
                        shipping_line1="x",
                        shipping_city="Madrid",
                        shipping_postal_code="28014",
                        shipping_country_code="ES",
                    )
                    s.add(order)
                    await s.flush()
                    await ReservationRepository(s).reserve_stock(
                        order_id=order.id,
                        warehouse_id=warehouse.id,
                        items=[RequestedItem(product_id=product.id, quantity=1)],
                    )
                return "won"
            except InsufficientStock:
                return "lost"

    results = await asyncio.gather(*(attempt() for _ in range(n)))

    assert results.count("won") == 1, f"expected exactly one winner, got {results}"
    assert results.count("lost") == n - 1
    assert len(set(backend_pids)) == n, "coroutines shared connections — not a real race"

    row = (await session.execute(select(Inventory))).scalar_one()
    await session.refresh(row)
    assert row.reserved == 1
    assert 0 <= row.reserved <= row.on_hand

    # The invariant across every row, not just the one under test.
    violations = await session.scalar(
        select(func.count()).select_from(Inventory).where(
            (Inventory.reserved < 0) | (Inventory.reserved > Inventory.on_hand)
        )
    )
    assert violations == 0


async def test_overlapping_multi_item_orders_do_not_deadlock(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Concurrent orders over overlapping products all complete.

    Each coroutine passes its items in a DIFFERENT order; the repository sorts
    them into the global lock order, which is what prevents the cycle. Without
    that sort this test raises DeadlockDetected (SQLSTATE 40P01).
    """
    warehouse = await make_warehouse(session, "MAD-01", at=MADRID)
    products = [await make_product(session, f"SKU-{i}") for i in range(3)]
    for p in products:
        await make_stock(session, warehouse, p, on_hand=100)
    customer = await make_customer(session)
    await session.commit()

    async def order_with(shuffled: list[RequestedItem]) -> None:
        async with session_factory() as s:
            async with s.begin():
                order = Order(
                    customer_id=customer.id,
                    warehouse_id=warehouse.id,
                    currency="EUR",
                    total_minor=0,
                    shipping_line1="x",
                    shipping_city="Madrid",
                    shipping_postal_code="28014",
                    shipping_country_code="ES",
                )
                s.add(order)
                await s.flush()
                await ReservationRepository(s).reserve_stock(
                    order_id=order.id, warehouse_id=warehouse.id, items=shuffled
                )

    items = [RequestedItem(product_id=p.id, quantity=1) for p in products]
    # 20 coroutines, each presenting the three products in a rotated order.
    tasks = [
        order_with(items[i % 3 :] + items[: i % 3])
        for i in range(20)
    ]
    await asyncio.gather(*tasks)  # a deadlock would raise here

    total_reserved = await session.scalar(select(func.sum(Inventory.reserved)))
    assert total_reserved == 60  # 20 orders x 3 products


async def test_failed_reservation_leaves_nothing_behind(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Reservation fails on the LAST item: no order, no items, inventory intact.

    Asserts on the database, not just the exception — a rollback that left rows
    behind would otherwise pass.
    """
    warehouse = await make_warehouse(session, "MAD-01", at=MADRID)
    plentiful = await make_product(session, "SKU-PLENTY")
    scarce = await make_product(session, "SKU-SCARCE")
    await make_stock(session, warehouse, plentiful, on_hand=100)
    await make_stock(session, warehouse, scarce, on_hand=1)
    customer = await make_customer(session)
    await session.commit()

    before = {
        (r.product_id): (r.on_hand, r.reserved)
        for r in (await session.execute(select(Inventory))).scalars()
    }

    async with session_factory() as s:
        with pytest.raises(InsufficientStock) as exc:
            async with s.begin():
                order = Order(
                    customer_id=customer.id,
                    warehouse_id=warehouse.id,
                    currency="EUR",
                    total_minor=0,
                    shipping_line1="x",
                    shipping_city="Madrid",
                    shipping_postal_code="28014",
                    shipping_country_code="ES",
                )
                s.add(order)
                await s.flush()
                await ReservationRepository(s).reserve_stock(
                    order_id=order.id,
                    warehouse_id=warehouse.id,
                    items=[
                        RequestedItem(product_id=plentiful.id, quantity=5),
                        RequestedItem(product_id=scarce.id, quantity=99),
                    ],
                )
    assert exc.value.product_id == scarce.id

    assert await session.scalar(select(func.count()).select_from(Order)) == 0
    assert (
        await session.scalar(select(func.count()).select_from(StockReservation)) == 0
    )
    after = {
        (r.product_id): (r.on_hand, r.reserved)
        for r in (await session.execute(select(Inventory))).scalars()
    }
    assert after == before, "inventory changed despite the rollback"


async def test_release_is_idempotent(session: AsyncSession) -> None:
    """Releasing twice does not double-decrement or drive reserved negative."""
    warehouse = await make_warehouse(session, "MAD-01", at=MADRID)
    product = await make_product(session, "SKU-KEYB")
    await make_stock(session, warehouse, product, on_hand=10)
    order = await _bare_order(session, warehouse.id)
    repo = ReservationRepository(session)
    await repo.reserve_stock(
        order_id=order.id,
        warehouse_id=warehouse.id,
        items=[RequestedItem(product_id=product.id, quantity=4)],
    )
    await session.commit()

    row = (await session.execute(select(Inventory))).scalar_one()
    await session.refresh(row)
    assert row.reserved == 4

    first = await repo.release_reservation(order.id)
    await session.commit()
    assert len(first) == 1

    second = await repo.release_reservation(order.id)
    await session.commit()
    assert second == [], "second release claimed rows it should not have"

    await session.refresh(row)
    assert row.reserved == 0
    assert row.reserved >= 0


async def test_expired_reservation_is_swept_and_stock_returns(
    session: AsyncSession,
) -> None:
    """The sweeper hands expired stock back, and it becomes reservable again."""
    warehouse = await make_warehouse(session, "MAD-01", at=MADRID)
    product = await make_product(session, "SKU-KEYB")
    await make_stock(session, warehouse, product, on_hand=1)
    order = await _bare_order(session, warehouse.id)
    repo = ReservationRepository(session)
    await repo.reserve_stock(
        order_id=order.id,
        warehouse_id=warehouse.id,
        items=[RequestedItem(product_id=product.id, quantity=1)],
    )
    await session.commit()

    # Age the hold past its expiry.
    await session.execute(
        update(StockReservation).values(
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)
        )
    )
    await session.commit()

    released = await repo.sweep_expired()
    await session.commit()
    assert len(released) == 1

    row = (await session.execute(select(Inventory))).scalar_one()
    await session.refresh(row)
    assert row.reserved == 0

    # And the freed unit can be reserved again.
    other = await _bare_order(session, warehouse.id)
    await repo.reserve_stock(
        order_id=other.id,
        warehouse_id=warehouse.id,
        items=[RequestedItem(product_id=product.id, quantity=1)],
    )
    await session.commit()
    await session.refresh(row)
    assert row.reserved == 1


async def test_concurrent_sweepers_release_exactly_once(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two workers sweeping at once must not double-decrement.

    The claim UPDATE is the mutual exclusion: the loser blocks, then re-evaluates
    status = 'active' against the committed row and matches nothing.
    """
    warehouse = await make_warehouse(session, "MAD-01", at=MADRID)
    product = await make_product(session, "SKU-KEYB")
    await make_stock(session, warehouse, product, on_hand=10)
    order = await _bare_order(session, warehouse.id)
    await ReservationRepository(session).reserve_stock(
        order_id=order.id,
        warehouse_id=warehouse.id,
        items=[RequestedItem(product_id=product.id, quantity=6)],
    )
    await session.execute(
        update(StockReservation).values(
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)
        )
    )
    await session.commit()

    async def sweep() -> int:
        async with session_factory() as s:
            async with s.begin():
                return len(await ReservationRepository(s).sweep_expired())

    counts = await asyncio.gather(sweep(), sweep())

    assert sorted(counts) == [0, 1], f"expected one sweeper to claim it, got {counts}"
    row = (await session.execute(select(Inventory))).scalar_one()
    await session.refresh(row)
    assert row.reserved == 0
