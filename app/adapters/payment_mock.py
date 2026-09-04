"""A deterministic mock payment provider.

NO RANDOMNESS ANYWHERE. Behaviour is chosen by the card's last four digits, the
same convention real gateways use for test cards, so a test forces an outcome by
picking a number rather than by patching. Reviewers re-run this; the same input
must always produce the same outcome.

    ...0002  declined
    ...0069  timeout        -> PaymentOutcome.unknown
    ...0119  provider error -> PaymentOutcome.unknown
    anything else            succeeded

THE CARD NEVER LEAVES THIS CLASS. get_secret_value() is called exactly once, to
take the last four digits and the brand. The full number is never logged, never
returned, and never persisted.
"""

import logging
from pydantic import SecretStr

from app.domain.payment import PaymentOutcome, PaymentResult

logger = logging.getLogger(__name__)

DECLINE_SUFFIX = "0002"
TIMEOUT_SUFFIX = "0069"
ERROR_SUFFIX = "0119"


def _brand(digits: str) -> str:
    if digits.startswith("4"):
        return "visa"
    if digits[:2] in {"51", "52", "53", "54", "55"}:
        return "mastercard"
    if digits[:2] in {"34", "37"}:
        return "amex"
    return "unknown"


class MockPaymentProvider:
    """Implements PaymentProvider. In-process, no network, no state beyond the
    charge log."""

    def __init__(self) -> None:
        # Every idempotency key this provider has been asked to charge, in
        # order. A test asserts len(...) == 1 to prove exactly one charge
        # happened across N concurrent callers — stronger than a call count,
        # which cannot tell a retry from a duplicate.
        self.charged_keys: list[str] = []
        self._results: dict[str, PaymentResult] = {}

    async def charge(
        self,
        *,
        card: SecretStr,
        amount_minor: int,
        currency: str,
        description: str,
        idempotency_key: str,
    ) -> PaymentResult:
        # The provider is idempotent too: the same key returns the same answer
        # without recording a second charge, exactly as a real gateway would.
        if idempotency_key in self._results:
            return self._results[idempotency_key]

        # The one and only read of the secret, to derive what we are allowed to
        # keep. The full value is not assigned to anything that outlives it.
        digits = card.get_secret_value().replace(" ", "").replace("-", "")
        last4, brand = digits[-4:], _brand(digits)

        self.charged_keys.append(idempotency_key)

        if last4 == DECLINE_SUFFIX:
            result = PaymentResult(
                outcome=PaymentOutcome.declined,
                failure_reason="card declined",
                card_brand=brand,
                card_last4=last4,
            )
        elif last4 == TIMEOUT_SUFFIX:
            result = PaymentResult(
                outcome=PaymentOutcome.unknown,
                failure_reason="provider timeout",
                card_brand=brand,
                card_last4=last4,
            )
        elif last4 == ERROR_SUFFIX:
            result = PaymentResult(
                outcome=PaymentOutcome.unknown,
                failure_reason="provider error",
                card_brand=brand,
                card_last4=last4,
            )
        else:
            result = PaymentResult(
                outcome=PaymentOutcome.succeeded,
                reference=f"mock_{idempotency_key[:12]}",
                card_brand=brand,
                card_last4=last4,
            )

        # Logged with the last four only — never the number, never the brand's
        # source value.
        logger.info(
            "charge %s: %s (%d %s, card ****%s)",
            idempotency_key,
            result.outcome.value,
            amount_minor,
            currency,
            last4,
        )

        # A timeout means the provider may still settle it. Recording the result
        # lets get_by_idempotency_key answer reconciliation later.
        self._results[idempotency_key] = result
        return result

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> PaymentResult | None:
        """What reconciliation asks when an outcome is unknown.

        In this mock a timeout stays unknown, so reconciliation correctly leaves
        the payment pending. A test can seed _results to simulate the provider
        having settled it in the meantime.
        """
        return self._results.get(idempotency_key)
