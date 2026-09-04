"""The checkout saga: idempotency, reservation, payment, confirmation.

TRANSACTION BOUNDARIES ARE THE WHOLE POINT OF THIS MODULE.

    ┌─ tx1 ──────────────────────────────────────────────────────┐
    │ claim idempotency key   INSERT ... ON CONFLICT DO NOTHING  │
    │ select warehouse, insert order + items, reserve stock      │
    │ transition pending -> reserved                             │
    │ INSERT payments (pending)          <- BEFORE the charge    │
    └─ COMMIT ───────────────────────────────────────────────────┘
                    ****  NO TRANSACTION OPEN  ****
              provider.charge(..., idempotency_key=str(order.id))
    ┌─ tx2 ──────────────────────────────────────────────────────┐
    │ succeeded: payment succeeded, reserved->paid->confirmed,   │
    │            reservations committed, outbox row, key done    │
    │ declined:  payment failed, release stock, ->payment_failed │
    │ unknown:   annotate only. STOCK STAYS HELD.                │
    └─ COMMIT ───────────────────────────────────────────────────┘

The payment call must never sit inside a transaction: it is a network call of
unbounded duration, and an open transaction would hold row locks on `inventory`
for its whole length. _assert_no_transaction() enforces that at runtime rather
than by comment, so a future refactor that wraps this in one transaction fails
loudly instead of silently degrading throughput.

UNKNOWN IS NOT FAILURE. A timeout leaves the reservation held, the order
reserved, and the idempotency key in_progress. Releasing stock on an unknown
outcome is how a customer who was charged loses their order.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.geo import to_point
from app.domain.address import PostalAddress
from app.domain.enums import IdempotencyStatus, OrderStatus, ReservationStatus
from app.domain.errors import (
    IdempotencyInProgress,
    IdempotencyKeyReuse,
    PaymentDeclined,
    PaymentUnresolved,
)
from app.domain.payment import PaymentOutcome, PaymentResult
from app.domain.selection import RequestedItem
from app.models import Order
from app.ports.geocoding import GeocodingProvider
from app.ports.payment import PaymentProvider
from app.repositories.idempotency import IdempotencyRepository
from app.repositories.order import OrderRepository
from app.repositories.outbox import OutboxRepository
from app.repositories.payment import PaymentRepository
from app.repositories.reservation import ReservationRepository
from app.schemas.order import OrderResponse
from app.services.order_creation import OrderCreationService

logger = logging.getLogger(__name__)

ORDER_CONFIRMED = "order_confirmed"


@dataclass(frozen=True, slots=True)
class CheckoutResult:
    """What the route needs, and exactly what a replay will return.

    The body is built ONCE, inside tx2, and both stored and returned. The 201
    body and any later replay are therefore the same object by construction
    rather than by two code paths agreeing.
    """

    order_id: UUID
    body: dict[str, Any]
    status_code: int


class ReplayedResponse(Exception):
    """Not an error: a completed key's stored response, raised to short-circuit.

    Modelled as an exception because it must unwind the handler exactly like the
    error cases do, and because a caller cannot accidentally ignore it.
    """

    def __init__(self, body: dict[str, Any], status_code: int) -> None:
        self.body = body
        self.status_code = status_code
        super().__init__(f"replaying stored response {status_code}")


class OrderCheckoutService:
    def __init__(
        self,
        session: AsyncSession,
        geocoder: GeocodingProvider,
        payments: PaymentProvider,
    ) -> None:
        self._session = session
        self._provider = payments
        self._creation = OrderCreationService(session=session, geocoder=geocoder)
        self._geocoder = geocoder
        self._idempotency = IdempotencyRepository(session)
        self._orders = OrderRepository(session)
        self._reservations = ReservationRepository(session)
        self._payments = PaymentRepository(session)
        self._outbox = OutboxRepository(session)

    async def checkout(
        self,
        *,
        idempotency_key: str,
        request_hash: str,
        customer_id: UUID,
        address: PostalAddress,
        items: Sequence[RequestedItem],
        card: SecretStr,
    ) -> CheckoutResult:
        """Run the saga. Returns the response body, or raises.

        Raises:
            IdempotencyKeyReuse, IdempotencyInProgress, ReplayedResponse
            PaymentDeclined, PaymentUnresolved
            plus everything order creation can raise.
        """
        # Geocode first: the only other network call, kept out of tx1.
        coordinates = await self._geocoder.geocode(address)
        shipping_point = to_point(lon=coordinates.lon, lat=coordinates.lat)

        # ---- tx1 -----------------------------------------------------------
        async with self._session.begin():
            await self._claim_key(key=idempotency_key, hashed=request_hash)

            order = await self._creation.create_in_transaction(
                customer_id=customer_id,
                address=address,
                items=items,
                shipping_point=shipping_point,
            )
            payment = await self._payments.create_pending(
                order_id=order.id,
                amount_minor=order.total_minor,
                currency=order.currency,
                description=f"Order {order.id}",
            )
            payment_id = payment.id
            amount_minor, currency = order.total_minor, order.currency
        # ---- committed -----------------------------------------------------

        self._assert_no_transaction()

        result = await self._provider.charge(
            card=card,
            amount_minor=amount_minor,
            currency=currency,
            description=f"Order {order.id}",
            # Deterministic and recomputable, so reconciliation can ask the
            # provider about this exact charge without storing another column.
            idempotency_key=str(order.id),
        )

        # ---- tx2 -----------------------------------------------------------
        async with self._session.begin():
            if result.outcome is PaymentOutcome.succeeded:
                body = await self._complete(
                    order, payment_id, result, idempotency_key
                )
            elif result.outcome is PaymentOutcome.declined:
                await self._compensate(order, payment_id, result, idempotency_key)
            else:
                await self._leave_unresolved(payment_id, result)

        if result.outcome is PaymentOutcome.declined:
            raise PaymentDeclined(result.failure_reason or "declined")
        if result.outcome is PaymentOutcome.unknown:
            raise PaymentUnresolved(result.failure_reason or "unknown")
        return CheckoutResult(order_id=order.id, body=body, status_code=201)

    # ------------------------------------------------------------------ tx1

    async def _claim_key(self, *, key: str, hashed: str) -> None:
        """Take the key, or decide what the existing one means.

        The hash check comes FIRST: reusing a key for a different body is wrong
        no matter how the first attempt ended.
        """
        if await self._idempotency.claim(key=key, hashed=hashed) is not None:
            return  # we own it

        existing = await self._idempotency.get(key)
        if existing is None:  # pragma: no cover - the row cannot vanish mid-tx
            raise IdempotencyInProgress(key)

        if existing.request_hash != hashed:
            raise IdempotencyKeyReuse(key)

        if existing.status is IdempotencyStatus.completed:
            raise ReplayedResponse(
                body=existing.response_body or {},
                status_code=existing.response_status or 200,
            )

        if existing.status is IdempotencyStatus.failed:
            # CAS back to in_progress; the loser of a concurrent retry falls
            # through to the 409 below rather than charging a second time.
            if await self._idempotency.reclaim_failed(key=key):
                return
        raise IdempotencyInProgress(key)

    # ------------------------------------------------------------------ tx2

    async def _complete(
        self,
        order: Order,
        payment_id: UUID,
        result: PaymentResult,
        key: str,
    ) -> dict[str, Any]:
        """Success. Shared with reconciliation so the two cannot drift."""
        await self._payments.mark_succeeded(
            payment_id=payment_id,
            reference=result.reference,
            card_brand=result.card_brand,
            card_last4=result.card_last4,
        )
        await self._orders.transition(
            order_id=order.id,
            from_status=OrderStatus.reserved,
            to_status=OrderStatus.paid,
            reason="payment succeeded",
        )
        await self._orders.transition(
            order_id=order.id,
            from_status=OrderStatus.paid,
            to_status=OrderStatus.confirmed,
            reason="order confirmed",
        )
        # The hold becomes a sale: committed, not released.
        await self._reservations.settle_for_order(
            order_id=order.id, status=ReservationStatus.committed
        )
        # Same transaction as the confirmation — that is the outbox pattern.
        await self._outbox.add(
            event_type=ORDER_CONFIRMED,
            aggregate_id=order.id,
            payload={
                "order_id": str(order.id),
                "customer_id": str(order.customer_id),
                "warehouse_id": str(order.warehouse_id),
                "total_minor": order.total_minor,
                "currency": order.currency,
                "payment_reference": result.reference,
            },
        )

        # Built inside tx2 from the committed state, then both stored and
        # returned — so a replay cannot differ from the original response.
        body = await self._render(order.id, result.reference)
        await self._idempotency.settle_completed(
            key=key,
            response_body=body,
            response_status=201,
            order_id=order.id,
        )
        return body

    async def _render(self, order_id: UUID, reference: str | None) -> dict[str, Any]:
        """Serialise the order exactly as the route would.

        Importing the response schema here is deliberate: it is what guarantees
        the stored body and the returned body are the same shape. Building a
        dict by hand would be a second definition of the response, free to drift
        from the first.
        """
        loaded = await self._orders.get_for_response(order_id)
        return OrderResponse.model_validate(
            {
                "id": loaded.id,
                "status": loaded.status,
                "warehouse": loaded.warehouse,
                "items": list(loaded.items),
                "total_minor": loaded.total_minor,
                "currency": loaded.currency,
                "payment_reference": reference,
                "created_at": loaded.created_at,
            }
        ).model_dump(mode="json")

    async def _compensate(
        self,
        order: Order,
        payment_id: UUID,
        result: PaymentResult,
        key: str,
    ) -> None:
        """Declined: the only outcome with a definite answer, so the only one
        that releases stock automatically."""
        await self._payments.mark_failed(
            payment_id=payment_id,
            failure_reason=result.failure_reason or "declined",
            card_brand=result.card_brand,
            card_last4=result.card_last4,
        )
        await self._reservations.release_reservation(order.id)
        await self._orders.transition(
            order_id=order.id,
            from_status=OrderStatus.reserved,
            to_status=OrderStatus.payment_failed,
            reason=result.failure_reason or "declined",
        )
        # failed, not completed: the key is retried rather than replayed, so no
        # response body is stored.
        await self._idempotency.mark_failed(key=key, order_id=order.id)

    async def _leave_unresolved(
        self, payment_id: UUID, result: PaymentResult
    ) -> None:
        """Unknown: annotate and change nothing else.

        The payment stays `pending`, the order stays `reserved`, the reservation
        stays `active`, and the idempotency key stays `in_progress` so a retry
        gets 409 rather than a second charge. The expiry sweeper is the backstop
        if reconciliation never runs.
        """
        await self._payments.mark_unknown(
            payment_id=payment_id,
            failure_reason=result.failure_reason or "unknown",
            card_brand=result.card_brand,
            card_last4=result.card_last4,
        )
        logger.warning(
            "payment %s outcome unknown (%s): stock stays held pending reconciliation",
            payment_id,
            result.failure_reason,
        )

    def _assert_no_transaction(self) -> None:
        """Runtime proof that the charge happens outside a transaction."""
        if self._session.in_transaction():
            raise RuntimeError(
                "payment provider called with an open transaction: this would "
                "hold inventory row locks across a network call"
            )
