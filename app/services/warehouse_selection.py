"""Warehouse selection orchestration.

No FastAPI imports. Depends on the GeocodingProvider *port*, never on an adapter
— wiring happens in api/.

THIS SERVICE OPENS NO TRANSACTION AND PERFORMS NO WRITES. Everything here is a
read. Transaction boundaries belong to the order service in phase 4.

THE RESULT IS ADVISORY. Selection runs without locking, so the warehouse it
returns may have lost the stock by the time the reservation runs. The atomic
conditional UPDATE in phase 4 is the sole authority on availability; this is only
a narrowing step. Phase 4 must handle 'the selected warehouse could not reserve'
rather than assuming success.
"""

import logging
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.geo import to_point
from app.domain.address import PostalAddress
from app.domain.errors import NoEligibleWarehouse, UnknownProduct
from app.domain.selection import RequestedItem, WarehouseCandidate
from app.ports.geocoding import GeocodingProvider
from app.repositories.product import ProductRepository
from app.repositories.warehouse import WarehouseRepository

logger = logging.getLogger(__name__)


class WarehouseSelectionService:
    def __init__(
        self,
        session: AsyncSession,
        geocoder: GeocodingProvider,
    ) -> None:
        # Repositories receive the session; they never create one.
        self._products = ProductRepository(session)
        self._warehouses = WarehouseRepository(session)
        self._geocoder = geocoder

    async def select(
        self,
        address: PostalAddress,
        items: Sequence[RequestedItem],
    ) -> WarehouseCandidate:
        """Resolve the address, verify the products, pick the best warehouse.

        Raises:
            GeocodingFailed: the address could not be resolved.
            UnknownProduct: one or more product ids do not exist.
            NoEligibleWarehouse: no single warehouse can supply everything.
        """
        if not items:
            raise NoEligibleWarehouse(requested_products=0, stocked_anywhere=0)

        requested_ids = {item.product_id for item in items}

        # Checked before selection so a typo'd id is reported as such rather
        # than silently failing the HAVING count and looking like 'out of stock'.
        known = await self._products.existing_ids(requested_ids)
        missing = requested_ids - known
        if missing:
            raise UnknownProduct(frozenset(missing))

        coordinates = await self._geocoder.geocode(address)
        point = to_point(lon=coordinates.lon, lat=coordinates.lat)

        candidate = await self._warehouses.select_best(point=point, items=items)
        if candidate is None:
            stocked = await self._warehouses.count_products_stocked_anywhere(
                requested_ids
            )
            raise NoEligibleWarehouse(
                requested_products=len(requested_ids),
                stocked_anywhere=stocked,
            )

        logger.info(
            "selected warehouse %s at %.0f m for %d product(s)",
            candidate.code,
            candidate.distance_m,
            len(requested_ids),
        )
        return candidate
