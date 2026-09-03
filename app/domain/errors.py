"""Domain exceptions.

Raised by services and repositories, mapped to HTTP status codes in api/ in a
later phase. Nothing here imports FastAPI or knows what a status code is.
"""

from uuid import UUID


class DomainError(Exception):
    """Base for everything this application raises deliberately."""


class GeocodingFailed(DomainError):
    """The address could not be resolved to coordinates."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"geocoding failed: {reason}")


class UnknownProduct(DomainError):
    """One or more requested product ids do not exist.

    Distinct from NoEligibleWarehouse on purpose: a typo'd product id and a
    genuinely unfulfillable basket are different problems with different fixes,
    and the HAVING count would make them look identical.
    """

    def __init__(self, product_ids: frozenset[UUID]) -> None:
        self.product_ids = product_ids
        listed = ", ".join(sorted(str(p) for p in product_ids))
        super().__init__(f"unknown product ids: {listed}")


class NoEligibleWarehouse(DomainError):
    """No single warehouse can supply every requested product in full.

    Carries counts so the API layer can explain *why* without re-querying:
    whether nothing stocks a given product at all, or whether the products are
    simply spread across several warehouses.
    """

    def __init__(self, requested_products: int, stocked_anywhere: int) -> None:
        self.requested_products = requested_products
        self.stocked_anywhere = stocked_anywhere
        super().__init__(
            f"no single warehouse can fulfill all {requested_products} product(s); "
            f"{stocked_anywhere} of them are stocked somewhere"
        )


class InsufficientStock(DomainError):
    """A product could not be reserved in the requested quantity.

    Raised by the reservation repository when the conditional UPDATE matches no
    row. That means either the warehouse never had enough, or it lost the stock
    between warehouse selection (a read, deliberately advisory) and the
    reservation. Both are the same answer to the caller: this basket cannot be
    fulfilled from this warehouse right now.
    """

    def __init__(self, product_id: UUID, requested: int) -> None:
        self.product_id = product_id
        self.requested = requested
        super().__init__(
            f"insufficient stock for product {product_id}: {requested} requested"
        )


class InvalidStateTransition(DomainError):
    """An order status change that the state machine does not permit.

    Carries both the expected and the actual status so the caller can tell a
    genuinely illegal edge (pending -> confirmed) from a concurrent modification
    (another transaction moved the order first). Both are refusals to write, but
    only the second is a race.
    """

    def __init__(self, order_id: UUID, expected: str, actual: str) -> None:
        self.order_id = order_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"order {order_id}: cannot move from {actual!r} to {expected!r}"
        )


class MixedCurrencyBasket(DomainError):
    """The requested products are not all priced in the same currency.

    The database does not enforce one currency per order, so summing the lines
    would silently produce a meaningless total. Rejecting is the only honest
    option until multi-currency is a real requirement.
    """

    def __init__(self, currencies: frozenset[str]) -> None:
        self.currencies = currencies
        listed = ", ".join(sorted(currencies))
        super().__init__(f"basket spans multiple currencies: {listed}")
