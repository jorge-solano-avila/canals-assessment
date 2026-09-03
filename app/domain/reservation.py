"""Value objects for the reservation lifecycle."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ReservedItem:
    product_id: UUID
    quantity: int


@dataclass(frozen=True, slots=True)
class ReservationResult:
    """What a successful reserve_stock produced.

    Only constructed on success: a partial reservation is never representable,
    because the repository raises and the transaction rolls back instead.
    """

    warehouse_id: UUID
    items: tuple[ReservedItem, ...]


@dataclass(frozen=True, slots=True)
class ReleasedItem:
    """One (warehouse, product, quantity) triple returned to available stock."""

    warehouse_id: UUID
    product_id: UUID
    quantity: int
