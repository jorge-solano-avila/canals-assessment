"""Product lookups."""

from collections.abc import Collection
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Product


class ProductRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def existing_ids(self, product_ids: Collection[UUID]) -> frozenset[UUID]:
        """Return the subset of product_ids that exist.

        Kept separate from selection on purpose. Folded into the selection
        statement, a non-existent product id would simply fail the HAVING count
        and be indistinguishable from 'no warehouse has enough stock' — two very
        different problems with very different fixes.
        """
        if not product_ids:
            return frozenset()
        rows = await self._session.scalars(
            select(Product.id).where(Product.id.in_(list(product_ids)))
        )
        return frozenset(rows)
