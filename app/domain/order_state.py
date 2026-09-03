"""The order state machine.

THIS TABLE IS THE ONLY SOURCE OF TRUTH for what status changes are legal.
Nothing else in the codebase assigns orders.status; every change goes through
OrderRepository.transition, which consults assert_allowed first.

pending ──> reserved ──> paid ──> confirmed
   │           │  │
   │           │  └──> payment_failed   (compensate: release the reservation)
   └───────────┴─────> cancelled        (compensate: release, if reserved)

`paid -> payment_failed` is deliberately absent. With the mandated
reserve -> commit -> charge -> commit sequence the order sits in `reserved`
while the charge runs, so a decline is reserved -> payment_failed. Keeping
`paid` reachable only on success means an order marked paid always has a
succeeded charge: the state can never contradict the money. A late reversal
(chargeback) would be a new edge added deliberately, not an accident.
"""

from app.domain.enums import OrderStatus

ALLOWED: frozenset[tuple[OrderStatus, OrderStatus]] = frozenset(
    {
        (OrderStatus.pending, OrderStatus.reserved),
        (OrderStatus.pending, OrderStatus.cancelled),
        (OrderStatus.reserved, OrderStatus.paid),
        (OrderStatus.reserved, OrderStatus.payment_failed),
        (OrderStatus.reserved, OrderStatus.cancelled),
        (OrderStatus.paid, OrderStatus.confirmed),
    }
)

TERMINAL: frozenset[OrderStatus] = frozenset(
    {
        OrderStatus.confirmed,
        OrderStatus.payment_failed,
        OrderStatus.cancelled,
    }
)


def is_allowed(from_status: OrderStatus, to_status: OrderStatus) -> bool:
    return (from_status, to_status) in ALLOWED


def allowed_from(from_status: OrderStatus) -> frozenset[OrderStatus]:
    """Every status reachable in one step. Used for error messages and tests."""
    return frozenset(to for (frm, to) in ALLOWED if frm == from_status)
