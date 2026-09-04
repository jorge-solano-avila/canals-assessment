"""Stock reservation, release and expiry.

ATOMICITY GUARANTEE — this is what callers depend on, and it is the reason this
module exists rather than a pile of ad-hoc UPDATEs:

    After reserve_stock returns, EITHER every requested item is reserved and a
    matching stock_reservations row exists for each, OR nothing changed at all.
    There is no partial reservation. An order can never hold some of its items.

That guarantee is DELIBERATELY POSTGRESQL-SPECIFIC. It rests on two properties
of this engine, not on portable SQL:

  1. An UPDATE that matches a row takes a row-level exclusive lock held until
     the transaction ends.
  2. Under READ COMMITTED, an UPDATE that blocks on a row locked by another
     transaction RE-EVALUATES its WHERE clause against the new row version once
     that transaction commits (EvalPlanQual), and skips the row if it no longer
     matches.

Property 2 is what makes the conditional UPDATE correct: two requests racing for
the last unit both reach the row, and the loser's `on_hand - reserved >= qty`
predicate is re-tested against the winner's committed value, matching nothing.
It is also why there is no SELECT ... FOR UPDATE anywhere here — that would add
a round trip and a window between reading and writing, for a guarantee the
UPDATE already provides.

Swapping this module for another database engine means re-deriving that
argument, not just retranslating the SQL.

DEADLOCK AVOIDANCE — every statement in this codebase that updates `inventory`
does so sorted by (warehouse_id, product_id). Reserve, release and sweep all
obey it, and any future writer must too.

    Why it is needed: two concurrent orders A{X,Y} and B{Y,X} would acquire
    locks in opposite order, forming a cycle. PostgreSQL detects it and aborts
    one with SQLSTATE 40P01. A total order on lock acquisition makes the cycle
    impossible.

    Why being conditional does not remove the need: a row failing the predicate
    is never locked and contributes no edge, which shrinks the surface — but two
    orders that both SUCCEED on overlapping products still lock both rows, and
    that is precisely the deadlock case. Conditional UPDATEs are not
    self-ordering.

There is deliberately NO retry on 40P01. Consistent ordering makes it
unreachable from our own paths, so a deadlock here would be a defect to fix,
not a condition to absorb.
"""

from collections.abc import Sequence
from datetime import timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.enums import ReservationStatus
from app.domain.errors import InsufficientStock
from app.domain.reservation import ReleasedItem, ReservationResult, ReservedItem
from app.domain.selection import RequestedItem
from app.models import Inventory, StockReservation


def _lock_order(items: Sequence[RequestedItem], warehouse_id: UUID) -> list[RequestedItem]:
    """Sort into the global lock-acquisition order.

    Warehouse is constant for one order, so product_id alone would do; sorting
    on the pair costs nothing and keeps the invariant true if a future path ever
    spans warehouses.
    """
    return sorted(items, key=lambda i: (warehouse_id, i.product_id))


class ReservationRepository:
    """Owns the consistency of the inventory / stock_reservations pair.

    Receives a session, never creates one. Never commits: the caller's
    transaction is what makes the guarantee above hold.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reserve_stock(
        self,
        *,
        order_id: UUID,
        warehouse_id: UUID,
        items: Sequence[RequestedItem],
    ) -> ReservationResult:
        """Reserve every item or raise, leaving the transaction to roll back.

        Raises:
            InsufficientStock: the first product that could not be reserved.
        """
        expires_at = func.now() + timedelta(seconds=settings.reservation_ttl_seconds)
        reserved: list[ReservedItem] = []

        for item in _lock_order(items, warehouse_id):
            # The availability check and the write are ONE statement. Never
            # read-check-write: any gap between them is a lost update.
            claimed = await self._session.scalar(
                update(Inventory)
                .where(
                    Inventory.warehouse_id == warehouse_id,
                    Inventory.product_id == item.product_id,
                    Inventory.on_hand - Inventory.reserved >= item.quantity,
                )
                .values(
                    reserved=Inventory.reserved + item.quantity,
                    updated_at=func.now(),
                )
                .returning(Inventory.id)
            )
            if claimed is None:
                # No row matched: either no inventory row for this pair, or not
                # enough available. Raising rolls back every earlier item.
                raise InsufficientStock(item.product_id, item.quantity)

            self._session.add(
                StockReservation(
                    order_id=order_id,
                    warehouse_id=warehouse_id,
                    product_id=item.product_id,
                    quantity=item.quantity,
                    status=ReservationStatus.active,
                    expires_at=expires_at,
                )
            )
            reserved.append(
                ReservedItem(product_id=item.product_id, quantity=item.quantity)
            )

        await self._session.flush()
        return ReservationResult(warehouse_id=warehouse_id, items=tuple(reserved))

    async def release_reservation(self, order_id: UUID) -> list[ReleasedItem]:
        """Return an order's held stock. Idempotent; a second call is a no-op.

        Idempotency comes from the claim statement's `status = 'active'`
        predicate: on a second call it matches nothing, so there is no row to
        decrement. `reserved` can never go negative because we only decrement by
        a quantity we previously incremented AND that is still recorded active,
        and both statements run in the caller's single transaction, so they
        cannot diverge.
        """
        return await self._settle(
            claim=update(StockReservation)
            .where(
                StockReservation.order_id == order_id,
                StockReservation.status == ReservationStatus.active,
            )
            .values(status=ReservationStatus.released, released_at=func.now()),
        )

    async def settle_for_order(
        self, *, order_id: UUID, status: ReservationStatus
    ) -> None:
        """Move an order's active holds to a terminal state WITHOUT returning
        stock to inventory.

        Used on confirmation: the hold becomes a sale, so `reserved` stays where
        it is — the units left the warehouse. Contrast release_reservation,
        which decrements. released_at is set because the phase-1 CHECK requires
        it for any non-active status; the column means "stopped holding", not
        strictly "was released".
        """
        await self._session.execute(
            update(StockReservation)
            .where(
                StockReservation.order_id == order_id,
                StockReservation.status == ReservationStatus.active,
            )
            .values(status=status, released_at=func.now())
        )
        await self._session.flush()

    async def sweep_expired(self, *, batch_size: int = 500) -> list[ReleasedItem]:
        """Release reservations whose hold has expired.

        Two workers cannot release the same row twice: the claim UPDATE blocks
        on the row lock, and when the first commits the second re-evaluates
        `status = 'active'` against the new version under READ COMMITTED. It no
        longer matches, so the second worker claims zero rows. The same
        mechanism that makes reservation correct makes this safe — no advisory
        lock and no SKIP LOCKED needed.

        Deliberately does NOT touch orders.status. Releasing stock and
        cancelling an order are different decisions.
        """
        expired_ids = (
            select(StockReservation.id)
            .where(
                StockReservation.status == ReservationStatus.active,
                StockReservation.expires_at < func.now(),
            )
            .order_by(StockReservation.expires_at)
            .limit(batch_size)
            .scalar_subquery()
        )
        return await self._settle(
            claim=update(StockReservation)
            .where(
                # `status = 'active'` MUST be on the outer statement, not only
                # inside the subquery above. READ COMMITTED re-evaluates the
                # OUTER qual against the new row version when a blocked UPDATE
                # resumes; a predicate buried in a subquery is not re-checked.
                # With only `id IN (...)` out here, a second sweeper unblocks,
                # finds the id still matches, and claims a row the first worker
                # already expired — double-decrementing inventory.
                StockReservation.status == ReservationStatus.active,
                StockReservation.id.in_(expired_ids),
            )
            .values(status=ReservationStatus.expired, released_at=func.now()),
        )

    async def _settle(self, *, claim: object) -> list[ReleasedItem]:
        """Claim reservations, then hand their quantities back to inventory.

        released_at is set on every exit from `active`, including `expired` —
        the phase-1 CHECK (status = 'active' OR released_at IS NOT NULL)
        requires it. The column means "when this stopped holding stock" rather
        than strictly "when it was released".
        """
        rows = (
            await self._session.execute(
                claim.returning(  # type: ignore[attr-defined]
                    StockReservation.warehouse_id,
                    StockReservation.product_id,
                    StockReservation.quantity,
                )
            )
        ).all()

        released = [
            ReleasedItem(warehouse_id=w, product_id=p, quantity=q) for w, p, q in rows
        ]

        # Sorted, for the same reason reserve_stock sorts. Per-row rather than a
        # single UPDATE ... FROM: PostgreSQL does not guarantee the order in
        # which UPDATE ... FROM processes its source rows, so one statement
        # could not honour the ordering the deadlock argument depends on.
        for item in sorted(released, key=lambda r: (r.warehouse_id, r.product_id)):
            decremented = await self._session.scalar(
                update(Inventory)
                .where(
                    Inventory.warehouse_id == item.warehouse_id,
                    Inventory.product_id == item.product_id,
                    # Belt and braces: the reserved >= 0 CHECK would catch this
                    # anyway, but a zero-row result is a condition we can raise
                    # on explicitly. Reaching it means the invariant is already
                    # broken, which must be loud rather than a constraint error.
                    Inventory.reserved >= item.quantity,
                )
                .values(
                    reserved=Inventory.reserved - item.quantity,
                    updated_at=func.now(),
                )
                .returning(Inventory.id)
            )
            if decremented is None:
                raise RuntimeError(
                    "inventory invariant violated: active reservation of "
                    f"{item.quantity} for product {item.product_id} exceeds "
                    f"reserved at warehouse {item.warehouse_id}"
                )

        await self._session.flush()
        return released
