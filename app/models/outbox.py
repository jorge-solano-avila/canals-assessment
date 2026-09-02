"""Events that must be published, written in the same transaction as the state change.

Guarantees the state change and its event commit together or not at all, which buys
at-least-once delivery without introducing a message broker.

DELIBERATELY NO FOREIGN KEY on aggregate_id — a deliberate exception to "FKs
everywhere". The outbox is an append-only log: a row must remain publishable even if
the aggregate is later removed, and an FK would let a delete cascade away events that
were never published.
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKeyMixin


class OutboxEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "outbox"

    event_type: Mapped[str] = mapped_column(String, nullable=False)
    aggregate_type: Mapped[str] = mapped_column(
        String, nullable=False, server_default=text("'order'")
    )
    aggregate_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    processed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        # The relay's poll query. Shrinks back to near-empty as the backlog drains.
        Index(
            "ix_outbox_unprocessed",
            "created_at",
            postgresql_where=text("processed_at IS NULL"),
        ),
    )
