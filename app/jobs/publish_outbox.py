"""Publish unprocessed outbox events.

A separate entrypoint, like the reservation sweeper:

    python -m app.jobs.publish_outbox        # or: make outbox

DELIVERY IS AT-LEAST-ONCE. Publishing happens inside the claiming transaction,
so a crash after the publish but before the commit republishes on the next poll.
That is the outbox pattern's real guarantee — consumers must be idempotent — and
it is stated rather than dressed up as exactly-once.

`attempts` is incremented in the same transaction, so a rollback loses the
increment and a genuinely poisonous event would loop. With a log sink that
cannot fail this is theoretical; a real broker needs attempts committed
separately plus a dead-letter threshold.
"""

import asyncio
import logging

from app.adapters.event_log import LoggingEventPublisher
from app.db.session import Session, engine
from app.ports.events import EventPublisher
from app.repositories.outbox import OutboxRepository

logger = logging.getLogger(__name__)


async def publish_once(
    publisher: EventPublisher, *, batch_size: int = 100
) -> int:
    """Claim and publish one batch. Returns how many were published."""
    async with Session() as session:
        async with session.begin():
            repo = OutboxRepository(session)
            # SELECT ... FOR UPDATE SKIP LOCKED: a row another worker holds is
            # invisible here, so N workers proceed in parallel instead of
            # queueing behind the same head-of-queue rows.
            batch = await repo.claim_batch(batch_size=batch_size)
            for event in batch:
                await publisher.publish(
                    event_type=event.event_type,
                    aggregate_id=event.aggregate_id,
                    payload=event.payload,
                )
            await repo.mark_processed([e.id for e in batch])
            return len(batch)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    publisher = LoggingEventPublisher()
    total = 0
    while (n := await publish_once(publisher)) > 0:
        total += n
    print(f"Published {total} outbox event(s).")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
