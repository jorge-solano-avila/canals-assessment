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
