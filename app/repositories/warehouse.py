"""Warehouse selection — the core query of this phase.

ONE statement, built with SQLAlchemy Core. No text() SQL, no ORM iteration, and
nothing filtered in Python: the all-products condition is a GROUP BY / HAVING in
the database.

ON THE GiST INDEX. ix_warehouses_location is *not* used by this query, and
cannot be. A KNN index scan has to drive the ordering over the base table, but
the rows being ordered here are aggregate output — a warehouse's eligibility is
unknown until its inventory has been aggregated, so there is no row set for the
index to order at the moment ordering is needed. Verified with EXPLAIN, including
with enable_seqscan and enable_sort both off. The index that actually carries
this query is ix_inventory_product_warehouse on (product_id, warehouse_id),
which serves the aggregate as a Bitmap Index Scan.

Measured, not assumed. With 5,000 warehouses all eligible, the plan is a top-N
heapsort over the aggregate output and the whole query runs in ~87 ms, of which
the GroupAggregate is ~20 ms — the sort is not the bottleneck. Forcing
enable_seqscan and enable_sort off at that size still does not produce a KNN
scan; the planner uses pk_warehouses and sorts. So the GiST index is not used
here at any scale, and the honest conclusion is that this query shape cannot use
it rather than that it merely prefers not to.

The eligibility aggregate is still kept in a CTE so the outer ORDER BY is over
`warehouses` rather than over aggregate output: it costs nothing, and it is the
shape a future ST_DWithin radius filter would need if warehouse counts ever make
that trade-off worthwhile.
"""

from collections.abc import Collection, Sequence
from uuid import UUID

from geoalchemy2 import WKBElement
from sqlalchemy import Float, Integer, Uuid, column, func, literal, select, values
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.selection import RequestedItem, WarehouseCandidate
from app.models import Inventory, Warehouse


class WarehouseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def select_best(
        self,
        *,
        point: WKBElement,
        items: Sequence[RequestedItem],
    ) -> WarehouseCandidate | None:
        """The single nearest warehouse able to supply every requested product.

        Returns None when nothing qualifies; the service turns that into
        NoEligibleWarehouse. This is a read with no locking, so the answer is
        advisory: stock can be taken between this query and the reservation.
        """
        if not items:
            return None

        distinct_products = {item.product_id for item in items}

        # Parameterized VALUES list — never string-interpolated. The explicit
        # column types give Postgres what it needs to resolve the join.
        requested = values(
            column("product_id", Uuid),
            column("qty", Integer),
            name="requested",
        ).data([(item.product_id, item.quantity) for item in items])

        available = Inventory.on_hand - Inventory.reserved

        eligible = (
            select(Inventory.warehouse_id)
            .join(requested, requested.c.product_id == Inventory.product_id)
            .where(available >= requested.c.qty)
            .group_by(Inventory.warehouse_id)
            .having(
                func.count(func.distinct(Inventory.product_id))
                == literal(len(distinct_products))
            )
            .cte("eligible")
        )

        # <-> is the KNN-capable distance operator on geography; ST_Distance
        # returns the value itself. Both are spheroidal meters.
        knn_distance = Warehouse.location.op("<->", return_type=Float)(point)

        stmt = (
            select(
                Warehouse.id,
                Warehouse.code,
                Warehouse.name,
                func.ST_Distance(Warehouse.location, point).label("distance_m"),
            )
            .where(
                Warehouse.id.in_(select(eligible.c.warehouse_id)),
                Warehouse.is_active.is_(True),
            )
            # w.code is the deterministic tie-break. w.id would be wrong: it is
            # gen_random_uuid(), so a tie-break assertion keyed on it would pass
            # or fail depending on which UUIDs that seeding happened to produce.
            .order_by(knn_distance, Warehouse.code)
            .limit(1)
        )

        row = (await self._session.execute(stmt)).first()
        if row is None:
            return None
        return WarehouseCandidate(
            id=row.id,
            code=row.code,
            name=row.name,
            distance_m=float(row.distance_m),
        )

    async def count_products_stocked_anywhere(
        self, product_ids: Collection[UUID]
    ) -> int:
        """How many of these products any warehouse has available at all.

        Only called on the failure path, so NoEligibleWarehouse can distinguish
        'nobody stocks the monitor' from 'the products are spread across several
        warehouses'.
        """
        if not product_ids:
            return 0
        stmt = select(func.count(func.distinct(Inventory.product_id))).where(
            Inventory.product_id.in_(list(product_ids)),
            Inventory.on_hand - Inventory.reserved > 0,
        )
        return int((await self._session.scalar(stmt)) or 0)
