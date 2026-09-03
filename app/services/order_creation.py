"""Order creation, up to `reserved`.

No FastAPI import. Depends on the geocoding port, never on an adapter.

TRANSACTION SHAPE. Geocoding happens FIRST, outside any transaction, because it
is the only step that can touch the network. Everything that touches the
database then runs in ONE transaction, so a failure anywhere leaves nothing
behind.

    geocode                             no transaction: the network call
    ── async with session.begin(): ──
       select warehouse                 reads (geocode inside is a cache hit)
       insert order                     status = pending
       insert order_items               prices SNAPSHOTTED
       reserve_stock                    atomic conditional UPDATEs
       transition pending -> reserved   compare-and-swap + history
    ── commit ──

The reads are inside the transaction rather than before it because SQLAlchemy
AUTOBEGINS on the first statement: doing them first would open a transaction
implicitly and make an explicit session.begin() afterwards raise "a transaction
is already begun". Including them is also harmless — plain SELECTs take no row
locks, so nothing is blocked by their being in scope.

The selection service geocodes internally as well. That second call is a cache
hit (CachedGeocoder) or free (MockGeocoder in tests), which is why the expensive
one is hoisted out here. If geocoding ever became an uncached network call,
selection should return the point it resolved rather than being asked twice.

The statement order is inverted from the obvious reading of "reserve then
insert the order": stock_reservations.order_id references orders.id, so the
order row must exist first. Same transaction, so the observable outcome is
identical.

SELECTION IS ADVISORY. If the chosen warehouse lost its stock between selection
and reservation, this service FAILS FAST with InsufficientStock. No retry, no
second warehouse. The window is microseconds, CLAUDE.md forbids retry/fallback
beyond what is specified, and a client retry re-runs selection against fresh
data — strictly better than looping over candidates computed from data already
known to be stale. The atomic UPDATE guarantees nothing is over-reserved when
this happens.
"""

import logging
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.geo import to_point
from app.domain.address import PostalAddress
from app.domain.enums import OrderStatus
from app.domain.errors import MixedCurrencyBasket
from app.domain.selection import RequestedItem
from app.models import Order
from app.ports.geocoding import GeocodingProvider
from app.repositories.order import OrderRepository
from app.repositories.product import ProductRepository
from app.repositories.reservation import ReservationRepository
from app.services.warehouse_selection import WarehouseSelectionService

logger = logging.getLogger(__name__)


class OrderCreationService:
    def __init__(
        self,
        session: AsyncSession,
        geocoder: GeocodingProvider,
    ) -> None:
        self._session = session
        self._selection = WarehouseSelectionService(session=session, geocoder=geocoder)
        self._products = ProductRepository(session)
        self._orders = OrderRepository(session)
        self._reservations = ReservationRepository(session)
        self._geocoder = geocoder

    async def create(
        self,
        *,
        customer_id: UUID,
        address: PostalAddress,
        items: Sequence[RequestedItem],
    ) -> Order:
        """Select, reserve and persist an order in `reserved`.

        Raises:
            GeocodingFailed, UnknownProduct, NoEligibleWarehouse: from selection.
            MixedCurrencyBasket: the products are not all one currency.
            InsufficientStock: the warehouse lost the race, or never had it.
        """
        # The one step that can touch the network, kept out of the transaction.
        coordinates = await self._geocoder.geocode(address)
        shipping_point = to_point(lon=coordinates.lon, lat=coordinates.lat)

        async with self._session.begin():
            # Selection also validates that every product exists, so the lookup
            # below cannot come back short.
            candidate = await self._selection.select(address, items)
            products = await self._products.get_by_ids(
                {item.product_id for item in items}
            )

            currencies = frozenset(p.currency for p in products.values())
            if len(currencies) > 1:
                # The database does not enforce one currency per order, so
                # summing across them would be silently meaningless.
                raise MixedCurrencyBasket(currencies)
            currency = next(iter(currencies))

            # Totals come from the snapshot values, never a live products read,
            # so a price change mid-request cannot make the total disagree with
            # the lines.
            total_minor = sum(
                products[item.product_id].unit_price_minor * item.quantity
                for item in items
            )

            order = await self._orders.create(
                customer_id=customer_id,
                warehouse_id=candidate.id,
                address=address,
                shipping_point=shipping_point,
                currency=currency,
                total_minor=total_minor,
            )
            await self._orders.add_items(
                order_id=order.id,
                products=products,
                items=[(i.product_id, i.quantity) for i in items],
            )
            await self._reservations.reserve_stock(
                order_id=order.id,
                warehouse_id=candidate.id,
                items=items,
            )
            await self._orders.transition(
                order_id=order.id,
                from_status=OrderStatus.pending,
                to_status=OrderStatus.reserved,
                reason="stock reserved",
            )

        logger.info(
            "order %s reserved at warehouse %s: %d line(s), %d %s",
            order.id,
            candidate.code,
            len(items),
            total_minor,
            currency,
        )
        return order
