"""Payment row lifecycle.

The row is INSERTED BEFORE THE CHARGE, in tx1. If it were created only after the
provider answers, a crash mid-charge would leave no record that money may have
moved — nothing for reconciliation to find, and a customer possibly billed for an
order that does not exist. Creating it `pending` first makes the intent durable
before the money moves.

`pending` therefore doubles as the `unknown` state: a payment that was sent and
never answered stays pending, with failure_reason recording why.
"""

from datetime import datetime, timedelta, timezone
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import PaymentStatus
from app.models import Payment


class PaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_pending(
        self,
        *,
        order_id: UUID,
        amount_minor: int,
        currency: str,
        description: str,
        provider: str = "mock",
    ) -> Payment:
        payment = Payment(
            order_id=order_id,
            status=PaymentStatus.pending,
            amount_minor=amount_minor,
            currency=currency,
            description=description,
            provider=provider,
        )
        self._session.add(payment)
        await self._session.flush()
        return payment

    async def mark_succeeded(
        self,
        *,
        payment_id: UUID,
        reference: str | None,
        card_brand: str | None,
        card_last4: str | None,
    ) -> None:
        await self._session.execute(
            update(Payment)
            .where(Payment.id == payment_id)
            .values(
                status=PaymentStatus.succeeded,
                provider_reference=reference,
                card_brand=card_brand,
                card_last4=card_last4,
                updated_at=func.now(),
            )
        )

    async def mark_failed(
        self,
        *,
        payment_id: UUID,
        failure_reason: str,
        card_brand: str | None = None,
        card_last4: str | None = None,
    ) -> None:
        # The phase-1 CHECK requires failure_reason whenever status is failed.
        await self._session.execute(
            update(Payment)
            .where(Payment.id == payment_id)
            .values(
                status=PaymentStatus.failed,
                failure_reason=failure_reason,
                card_brand=card_brand,
                card_last4=card_last4,
                updated_at=func.now(),
            )
        )

    async def mark_unknown(
        self,
        *,
        payment_id: UUID,
        failure_reason: str,
        card_brand: str | None = None,
        card_last4: str | None = None,
    ) -> None:
        """Annotate a payment whose outcome the provider never told us.

        Status STAYS pending — that is what unknown means here. Only the reason
        is recorded, so reconciliation and a human can both see what happened.
        """
        await self._session.execute(
            update(Payment)
            .where(Payment.id == payment_id)
            .values(
                failure_reason=failure_reason,
                card_brand=card_brand,
                card_last4=card_last4,
                updated_at=func.now(),
            )
        )

    async def unresolved_before(self, *, grace_seconds: int) -> Sequence[Payment]:
        """Payments still pending past the grace period — reconciliation input."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=grace_seconds)
        rows = await self._session.scalars(
            select(Payment).where(
                Payment.status == PaymentStatus.pending,
                Payment.created_at < cutoff,
            )
        )
        return list(rows)
