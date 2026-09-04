"""Record that a client request has already been seen.

Makes a retried POST /orders — flaky network, impatient user, double-clicked button
— return the original order instead of placing a second one and charging twice.

State is STORED, not derived (migration 0003). in_progress means a request owns
this key right now; completed means the stored response is replayed verbatim;
failed means the attempt settled badly and a retry is permitted.

Phase 2 briefly derived state from `response_status IS NULL`, which could express
settled vs unsettled but not "failed, retry allowed" apart from "settled, replay
forever". Paying for a third state buys that distinction back.

A stale in_progress row (a worker died mid-charge) is deliberately NOT
auto-released: the payment outcome is unknown, and letting a second attempt
through could double-charge. Reconciliation resolves it.
"""

import uuid
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import IDEMPOTENCY_STATUS, IdempotencyStatus


class IdempotencyKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "idempotency_keys"

    # The ON CONFLICT DO NOTHING target.
    key: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    request_hash: Mapped[str] = mapped_column(String, nullable=False)

    status: Mapped[IdempotencyStatus] = mapped_column(
        IDEMPOTENCY_STATUS, nullable=False, server_default=text("'in_progress'")
    )

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
        CheckConstraint(
            "status <> 'completed' OR "
            "(response_body IS NOT NULL AND response_status IS NOT NULL)",
            name="completed_has_response",
        ),
        # Retention sweep for keys abandoned by a crashed worker. Partial, so it
        # stays small: settled keys drop out of it.
        Index(
            "ix_idempotency_keys_in_progress",
            "created_at",
            postgresql_where=text("status = 'in_progress'"),
        ),
    )
