"""The payment port.

The card arrives as SecretStr and goes no further than the adapter. Only a
reference and the last four digits are ever persisted.
"""

from typing import Protocol

from pydantic import SecretStr

from app.domain.payment import PaymentResult


class PaymentProvider(Protocol):
    async def charge(
        self,
        *,
        card: SecretStr,
        amount_minor: int,
        currency: str,
        description: str,
        idempotency_key: str,
    ) -> PaymentResult:
        """Attempt a charge.

        Never raises for a business outcome: a decline is
        PaymentOutcome.declined, and a timeout or provider fault is
        PaymentOutcome.unknown. Callers must handle unknown without guessing.
        """
        ...

    async def get_by_idempotency_key(
        self, idempotency_key: str
    ) -> PaymentResult | None:
        """Ask the provider what happened to a charge we are unsure about.

        Not part of the original scope, but reconciliation cannot exist without
        it: resolving an unknown outcome means asking the authority.
        """
        ...
