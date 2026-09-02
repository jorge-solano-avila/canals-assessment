"""Record that a client request has already been seen.

Makes a retried POST /orders — flaky network, impatient user, double-clicked button
— return the original order instead of placing a second one and charging twice.

THERE IS NO status COLUMN. State is derived from one fact: response_status IS NULL
means the request is still settling, and a non-NULL value means it settled and the
stored response can be replayed. A definite failure is stored as its own 4xx/5xx
response, so a retry replays that error rather than re-running the operation.

The cost of that choice: "another request is charging right now" and "a worker
crashed mid-charge" both read as response_status IS NULL. The sweeper distinguishes
them with a time cutoff rather than a stored fact.
"""

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class IdempotencyKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "idempotency_keys"

    # The ON CONFLICT DO NOTHING target.
    key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    request_hash: Mapped[str] = mapped_column(String, nullable=False)

    response_body: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Convenience pointer for replaying a stored response; response_body already
    # holds what gets returned, so losing this on delete is harmless.
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint("char_length(request_hash) = 64", name="request_hash_sha256"),
        CheckConstraint(
            "response_status BETWEEN 100 AND 599", name="response_status_range"
        ),
        # The two response columns are written together or not at all, which is
        # what makes the derived-state rule above trustworthy.
        CheckConstraint(
            "(response_body IS NULL) = (response_status IS NULL)",
            name="response_columns_paired",
        ),
        Index("ix_idempotency_keys_created_at", "created_at"),
        # Sweeper that reclaims keys abandoned by a crashed worker. Partial, so it
        # stays small: settled keys drop out of it.
        Index(
            "ix_idempotency_keys_unsettled",
            "created_at",
            postgresql_where=text("response_status IS NULL"),
        ),
    )
