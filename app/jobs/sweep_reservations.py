"""Release reservations whose hold has expired.

A SEPARATE ENTRYPOINT, not a background task inside the API. It therefore runs
exactly once regardless of how many API workers exist, is testable by calling
sweep_once directly, and cannot take the API down with it.

    python -m app.jobs.sweep_reservations        # or: make sweep

IT IS NOT RUNNING UNLESS SOMETHING SCHEDULES IT. Cron, a compose service, or a
manual invocation — the README says so rather than implying a daemon exists.

It deliberately does not change orders.status: releasing stock and cancelling an
order are different decisions, and an order whose hold expired may still be
mid-charge. Phase 5 resolves that.
"""

import asyncio
import logging

from app.db.session import Session, engine
from app.repositories.reservation import ReservationRepository

logger = logging.getLogger(__name__)


async def sweep_once(*, batch_size: int = 500) -> int:
    """Release one batch of expired reservations. Returns rows released."""
    async with Session() as session:
        async with session.begin():
            released = await ReservationRepository(session).sweep_expired(
                batch_size=batch_size
            )
    if released:
        logger.info("swept %d expired reservation(s)", len(released))
    return len(released)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    total = 0
    # Drain in batches so one run cannot hold an unbounded transaction open.
    while (n := await sweep_once()) > 0:
        total += n
    print(f"Released {total} expired reservation(s).")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
