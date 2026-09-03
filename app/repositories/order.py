"""Order persistence and the only path that writes orders.status.

CONCURRENCY-SAFE TRANSITIONS. The status change is a compare-and-swap:

    UPDATE orders SET status = :to
    WHERE id = :id AND status = :expected
    RETURNING id

Zero rows means another transaction moved the order first. The WHERE clause is
the optimistic lock, so there is no SELECT ... FOR UPDATE and no lost update:
two concurrent transitions on one order cannot both succeed.

Every transition also writes an order_status_history row in the same
transaction, so the history can never disagree with orders.status.
"""

from collections.abc import Sequence
from uuid import UUID

from geoalchemy2 import WKBElement
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.address import PostalAddress
from app.domain.enums import OrderStatus
from app.domain.errors import InvalidStateTransition
from app.domain.order_state import is_allowed
from app.models import Order, OrderItem, OrderStatusHistory, Product


class OrderRepository:
    """Receives a session, never creates one, never commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        customer_id: UUID,
        warehouse_id: UUID,
        address: PostalAddress,
        shipping_point: WKBElement,
        currency: str,
        total_minor: int,
    ) -> Order:
        """Insert the order at the column default of `pending`.

        Deliberately does not accept a status. The row is created pending and
        reaches any other state only through transition(), so there is no code
        path that assigns a status outside the state machine.
        """
        order = Order(
            customer_id=customer_id,
            warehouse_id=warehouse_id,
            currency=currency,
            total_minor=total_minor,
            shipping_line1=address.line1,
            shipping_line2=address.line2,
            shipping_city=address.city,
            shipping_postal_code=address.postal_code,
            shipping_country_code=address.country_code,
            shipping_point=shipping_point,
        )
        self._session.add(order)
        await self._session.flush()

        # Creation row: from_status NULL, which the phase-1 CHECK
        # (from_status IS DISTINCT FROM to_status) permits.
        self._session.add(
            OrderStatusHistory(
                order_id=order.id,
                from_status=None,
                to_status=OrderStatus.pending,
                reason="order created",
            )
        )
        await self._session.flush()
        return order

    async def add_items(
        self,
        *,
        order_id: UUID,
        products: dict[UUID, Product],
        items: Sequence[tuple[UUID, int]],
    ) -> None:
        """Insert the lines, snapshotting price, currency, SKU and name.

        The snapshot is the point: rendering this order years from now must not
        join back to products and pick up today's price or a renamed SKU.
        line_total_minor is a generated column and is deliberately not set here.
        """
        for product_id, quantity in items:
            product = products[product_id]
            self._session.add(
                OrderItem(
                    order_id=order_id,
                    product_id=product.id,
                    product_sku=product.sku,
                    product_name=product.name,
                    quantity=quantity,
                    unit_price_minor=product.unit_price_minor,
                    currency=product.currency,
                )
            )
        await self._session.flush()

    async def transition(
        self,
        *,
        order_id: UUID,
        from_status: OrderStatus,
        to_status: OrderStatus,
        reason: str | None = None,
    ) -> None:
        """Move an order between states. The only writer of orders.status.

        Raises:
            InvalidStateTransition: the edge is not in the table, or another
                transaction changed the status first.
        """
        # Checked in Python first so an illegal edge gets a precise error
        # without a database round trip, and without ever reaching the CAS.
        if not is_allowed(from_status, to_status):
            raise InvalidStateTransition(
                order_id, expected=to_status.value, actual=from_status.value
            )

        moved = await self._session.scalar(
            update(Order)
            .where(Order.id == order_id, Order.status == from_status)
            .values(status=to_status, updated_at=func.now())
            .returning(Order.id)
        )
        if moved is None:
            # The CAS lost. Read the real status so the error says what actually
            # happened rather than guessing.
            actual = await self._session.scalar(
                select(Order.status).where(Order.id == order_id)
            )
            raise InvalidStateTransition(
                order_id,
                expected=to_status.value,
                actual=actual.value if actual is not None else "missing",
            )

        self._session.add(
            OrderStatusHistory(
                order_id=order_id,
                from_status=from_status,
                to_status=to_status,
                reason=reason,
            )
        )
        await self._session.flush()
