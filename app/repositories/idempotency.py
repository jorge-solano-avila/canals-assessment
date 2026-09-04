"""Idempotency key claiming and settlement.

THE CLAIM IS INSERT ... ON CONFLICT DO NOTHING RETURNING id, issued in the SAME
transaction as the order insert. That is what makes the key and the order atomic
with each other: a key can never exist for an order that was rolled back, and an
order can never exist without its key.

Idempotency lives entirely in PostgreSQL: the same transaction that inserts
the order inserts the key, which is what makes them atomic with each other.
No external store is involved, and none could provide that guarantee.
"""

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import null, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import IdempotencyStatus
from app.models import IdempotencyKey


def request_hash(payload: dict[str, Any]) -> str:
    """Stable sha256 of the request body.

    sort_keys so a client reordering JSON fields is not treated as a different
    request. The card number is excluded before this is called — the hash is
    computed from the serialised schema, which has exclude=True on that field.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class IdempotencyRepository:
    """Receives a session, never creates one, never commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(self, *, key: str, hashed: str) -> UUID | None:
        """Try to take ownership of this key.

        Returns the new row id if we won, None if the key already exists. The
        conflict is resolved without raising, so the caller's transaction stays
        usable — an IntegrityError would poison it and force a rollback before
        we could read the existing row.
        """
        claimed: UUID | None = await self._session.scalar(
            insert(IdempotencyKey)
            .values(
                key=key,
                request_hash=hashed,
                status=IdempotencyStatus.in_progress,
            )
            .on_conflict_do_nothing(index_elements=["key"])
            .returning(IdempotencyKey.id)
        )
        return claimed

    async def get(self, key: str) -> IdempotencyKey | None:
        row: IdempotencyKey | None = await self._session.scalar(
            select(IdempotencyKey).where(IdempotencyKey.key == key)
        )
        return row

    async def reclaim_failed(self, *, key: str) -> bool:
        """Move a failed key back to in_progress so it can be retried.

        A compare-and-swap, not a plain UPDATE: two concurrent retries of the
        same failed key would otherwise both proceed and both charge. The loser
        gets False, re-reads the row as in_progress, and is refused with 409.
        """
        reclaimed = await self._session.scalar(
            update(IdempotencyKey)
            .where(
                IdempotencyKey.key == key,
                IdempotencyKey.status == IdempotencyStatus.failed,
            )
            .values(
                # null(), not None: SQLAlchemy renders Python None into a JSONB
                # column as the JSON value 'null', which is NOT SQL NULL. That
                # would leave response_body non-null while response_status is
                # null and violate the paired-columns CHECK.
                status=IdempotencyStatus.in_progress,
                response_body=null(),
                response_status=null(),
            )
            .returning(IdempotencyKey.id)
        )
        return reclaimed is not None

    async def settle_completed(
        self,
        *,
        key: str,
        response_body: dict[str, Any],
        response_status: int,
        order_id: UUID,
    ) -> None:
        """Store the response a replay will return verbatim.

        Both response columns are written together — the phase-1 CHECK requires
        it, and migration 0003 additionally forbids a completed key without a
        response, because that would be a promise it cannot keep.
        """
        await self._session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.key == key)
            .values(
                status=IdempotencyStatus.completed,
                response_body=response_body,
                response_status=response_status,
                order_id=order_id,
            )
        )

    async def mark_failed(self, *, key: str, order_id: UUID | None = None) -> None:
        """Settle as failed, leaving the response columns NULL.

        A failed key is retried, never replayed, so there is no stored body to
        return. Leaving both NULL also satisfies the paired-columns CHECK
        without inventing a response nobody reads.
        """
        await self._session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.key == key)
            .values(
                # null(), not None — see reclaim_failed. A JSONB column given
                # Python None stores JSON 'null', which is not SQL NULL.
                status=IdempotencyStatus.failed,
                response_body=null(),
                response_status=null(),
                order_id=order_id,
            )
        )
