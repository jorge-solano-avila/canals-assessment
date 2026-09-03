"""The order state machine and end-to-end order creation."""

import asyncio

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.geocoding_mock import MockGeocoder
from app.domain.address import PostalAddress
from app.domain.enums import OrderStatus
from app.domain.errors import InvalidStateTransition
from app.domain.geo import Coordinates
from app.domain.order_state import allowed_from, is_allowed
from app.models import Inventory, Order, OrderItem, OrderStatusHistory, StockReservation
from app.repositories.order import OrderRepository
from app.services.order_creation import OrderCreationService
from tests.conftest import items, make_customer, make_product, make_stock, make_warehouse

MADRID_PT = Coordinates(lat=40.4168, lon=-3.7038)
MADRID = PostalAddress(
    line1="Calle de Alcala 45", city="Madrid", postal_code="28014", country_code="ES"
)


def test_transition_table_is_what_the_plan_says() -> None:
    """The table itself, pinned. paid -> payment_failed is deliberately absent."""
    assert allowed_from(OrderStatus.pending) == {
        OrderStatus.reserved,
        OrderStatus.cancelled,
    }
    assert allowed_from(OrderStatus.reserved) == {
        OrderStatus.paid,
        OrderStatus.payment_failed,
        OrderStatus.cancelled,
    }
    assert allowed_from(OrderStatus.paid) == {OrderStatus.confirmed}
    assert not is_allowed(OrderStatus.paid, OrderStatus.payment_failed)
    # Terminal states go nowhere.
    for terminal in (OrderStatus.confirmed, OrderStatus.cancelled, OrderStatus.payment_failed):
        assert allowed_from(terminal) == frozenset()


async def test_illegal_transition_raises_and_writes_nothing(
    session: AsyncSession,
) -> None:
    """pending -> confirmed is refused, and the row is untouched."""
    warehouse = await make_warehouse(session, "MAD-01", at=MADRID_PT)
    customer = await make_customer(session)
    order = await OrderRepository(session).create(
        customer_id=customer.id,
        warehouse_id=warehouse.id,
        address=MADRID,
        shipping_point=None,  # type: ignore[arg-type]
        currency="EUR",
        total_minor=0,
    )
    await session.commit()

    with pytest.raises(InvalidStateTransition):
        await OrderRepository(session).transition(
            order_id=order.id,
            from_status=OrderStatus.pending,
            to_status=OrderStatus.confirmed,
        )
    await session.rollback()

    status = await session.scalar(select(Order.status).where(Order.id == order.id))
    assert status == OrderStatus.pending, "status changed despite the refusal"
    # Only the creation history row exists.
    assert (
        await session.scalar(select(func.count()).select_from(OrderStatusHistory)) == 1
    )


async def test_concurrent_transitions_only_one_wins(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two transactions moving one order: the compare-and-swap decides."""
    warehouse = await make_warehouse(session, "MAD-01", at=MADRID_PT)
    customer = await make_customer(session)
    order = await OrderRepository(session).create(
        customer_id=customer.id,
        warehouse_id=warehouse.id,
        address=MADRID,
        shipping_point=None,  # type: ignore[arg-type]
        currency="EUR",
        total_minor=0,
    )
    await session.commit()

    async def cancel() -> str:
        async with session_factory() as s:
            try:
                async with s.begin():
                    await OrderRepository(s).transition(
                        order_id=order.id,
                        from_status=OrderStatus.pending,
                        to_status=OrderStatus.cancelled,
                    )
                return "won"
            except InvalidStateTransition:
                return "lost"

    results = await asyncio.gather(*(cancel() for _ in range(4)))

    assert results.count("won") == 1, f"lost update: {results}"
    status = await session.scalar(select(Order.status).where(Order.id == order.id))
    assert status == OrderStatus.cancelled


async def test_order_creation_persists_reserved_order(session: AsyncSession) -> None:
    """The deliverable: select, reserve, persist, all in one transaction."""
    warehouse = await make_warehouse(session, "MAD-01", at=MADRID_PT)
    keyboard = await make_product(session, "SKU-KEYB", price=8900)
    mouse = await make_product(session, "SKU-MOUSE", price=3450)
    await make_stock(session, warehouse, keyboard, on_hand=10)
    await make_stock(session, warehouse, mouse, on_hand=10)
    customer = await make_customer(session)
    await session.commit()

    service = OrderCreationService(session=session, geocoder=MockGeocoder())
    order = await service.create(
        customer_id=customer.id,
        address=MADRID,
        items=items((keyboard.id, 2), (mouse.id, 1)),
    )

    fresh = await session.scalar(select(Order).where(Order.id == order.id))
    assert fresh is not None
    await session.refresh(fresh)
    assert fresh.status == OrderStatus.reserved
    assert fresh.warehouse_id == warehouse.id
    # Totals from the snapshots: 2 x 8900 + 1 x 3450
    assert fresh.total_minor == 21250
    assert fresh.currency == "EUR"

    lines = (await session.scalars(select(OrderItem))).all()
    assert len(lines) == 2
    assert {line.product_sku for line in lines} == {"SKU-KEYB", "SKU-MOUSE"}
    # line_total_minor is the generated column doing its job.
    assert sum(line.line_total_minor for line in lines) == 21250

    reservations = (await session.scalars(select(StockReservation))).all()
    assert len(reservations) == 2
    assert all(r.expires_at is not None for r in reservations)

    stock = {
        r.product_id: r.reserved
        for r in (await session.execute(select(Inventory))).scalars()
    }
    assert stock[keyboard.id] == 2
    assert stock[mouse.id] == 1

    # One creation row plus one transition row.
    history = (await session.scalars(select(OrderStatusHistory))).all()
    assert len(history) == 2
    assert [h.to_status for h in sorted(history, key=lambda h: h.occurred_at)] == [
        OrderStatus.pending,
        OrderStatus.reserved,
    ]
