"""Transactional outbox.

The event row is written in the SAME transaction as the state change it
describes, so the two commit together or not at all. That is the entire point of
the pattern: no broker call inside a transaction, and no state change without its
event.

CLAIMING: SELECT ... FOR UPDATE SKIP LOCKED.

Deliberately different from the reservation sweeper, which uses a plain
conditional UPDATE. They want opposite things. The sweeper is a batch job where a
second worker briefly BLOCKING is harmless. The outbox is a QUEUE: workers should
run in parallel, and blocking would serialise them onto the same head-of-queue
rows, turning N workers into one. SKIP LOCKED makes a locked row invisible to the
next worker so it moves straight past.

DELIVERY IS AT-LEAST-ONCE. Publish happens inside the claiming transaction, so a
crash after publishing but before commit republishes on the next poll. That is
the outbox pattern's real guarantee; consumers must be idempotent.
"""

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import OutboxEvent


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        event_type: str,
        aggregate_id: UUID,
        payload: dict[str, Any],
        aggregate_type: str = "order",
    ) -> None:
        self._session.add(
            OutboxEvent(
                event_type=event_type,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                payload=payload,
            )
        )
        await self._session.flush()

    async def claim_batch(self, *, batch_size: int = 100) -> Sequence[OutboxEvent]:
        """Take a batch no other worker can take concurrently."""
        rows = await self._session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.processed_at.is_(None))
            .order_by(OutboxEvent.created_at)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        return list(rows)

    async def mark_processed(self, ids: Sequence[UUID]) -> None:
        if not ids:
            return
        await self._session.execute(
            update(OutboxEvent)
            .where(OutboxEvent.id.in_(list(ids)))
            .values(processed_at=func.now(), attempts=OutboxEvent.attempts + 1)
        )
