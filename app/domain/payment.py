"""Payment outcomes as the domain sees them.

`unknown` is a FIRST-CLASS outcome, not an error. A provider that times out has
not told us whether the money moved, and treating that as a failure is how you
release stock for an order the customer was charged for.

The database expresses `unknown` as payments.status = 'pending': the row is
inserted pending before the charge and only leaves that state when the provider
answers. Nothing was added to the payment_status enum.
"""

import enum
from dataclasses import dataclass


class PaymentOutcome(str, enum.Enum):
    succeeded = "succeeded"
    declined = "declined"
    unknown = "unknown"


@dataclass(frozen=True, slots=True)
class PaymentResult:
    outcome: PaymentOutcome
    reference: str | None = None
    failure_reason: str | None = None
    card_brand: str | None = None
    card_last4: str | None = None
