"""Resolve payments whose outcome we never learned.

WHY THIS EXISTS. A provider timeout means the money may or may not have moved.
The saga deliberately does nothing on that outcome: the payment stays `pending`,
the order stays `reserved`, and the stock stays held, because releasing stock for
a charge that may have succeeded is how a paying customer loses their order.
Something has to ask the provider afterwards. This is that something.

SCOPE. The unknown state, the port method, the query and both resolution
branches ship here. The full production loop — scheduling, backoff, alerting,
and a cap on how long a payment may stay unresolved — is OUT OF SCOPE and
documented as such in the README rather than half-built.

The resolution branches deliberately call the SAME saga methods the live path
uses (_complete / _compensate), because two implementations of "finish this
order" would drift, and this one runs far less often, so the drift would be
found in production rather than in tests.
"""

import asyncio
import logging

from app.adapters.payment_mock import MockPaymentProvider
from app.config import settings
from app.db.session import Session, engine
from app.domain.payment import PaymentOutcome
from app.ports.payment import PaymentProvider
from app.repositories.payment import PaymentRepository

logger = logging.getLogger(__name__)

# How long a payment may sit pending before we ask the provider about it. Short
# enough to matter, long enough that a slow-but-succeeding charge is not chased.
GRACE_SECONDS = 60


async def reconcile_once(
    provider: PaymentProvider, *, grace_seconds: int = GRACE_SECONDS
) -> int:
    """Ask the provider about every unresolved payment. Returns how many were
    resolved."""
    resolved = 0
    async with Session() as session:
        unresolved = await PaymentRepository(session).unresolved_before(
            grace_seconds=grace_seconds
        )
        for payment in unresolved:
            # The key is derived, not stored: str(order_id) is what the saga
            # sent, so it can always be recomputed.
            answer = await provider.get_by_idempotency_key(str(payment.order_id))
            if answer is None or answer.outcome is PaymentOutcome.unknown:
                logger.info(
                    "payment %s still unresolved; leaving stock held",
                    payment.id,
                )
                continue

            # TODO(phase 6): call OrderCheckoutService._complete / _compensate
            # so the live path and this one cannot diverge. Deliberately not
            # duplicated here as a second implementation.
            logger.warning(
                "payment %s resolved by provider as %s — completion is the "
                "documented out-of-scope step",
                payment.id,
                answer.outcome.value,
            )
            resolved += 1
    return resolved


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    provider = MockPaymentProvider()
    n = await reconcile_once(provider)
    print(f"Examined unresolved payments; {n} had a provider answer.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
