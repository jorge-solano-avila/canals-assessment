"""Inputs and outputs of warehouse selection.

WarehouseCandidate is a query projection, not a domain entity mirroring the
Warehouse model: it carries distance_m, which is computed by the query and is
not a column, so an ORM instance cannot express it without an ad-hoc attribute.
"""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RequestedItem:
    product_id: UUID
    quantity: int

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError(f"quantity must be positive, got {self.quantity}")


@dataclass(frozen=True, slots=True)
class WarehouseCandidate:
    id: UUID
    code: str
    name: str
    distance_m: float
