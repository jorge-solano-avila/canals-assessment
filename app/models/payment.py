"""Record of each charge attempt against the external payment provider.

THERE IS DELIBERATELY NO COLUMN FOR THE CARD NUMBER. The brief's payment API takes a
PAN; it arrives in the request body, is passed straight to the payment interface, and
is never written to a table or a log line. card_last4 and card_brand are the only
things retained — what a support agent actually needs to identify a charge. Storing a
PAN would drag the whole database into PCI-DSS scope, and the schema should make that
impossible by construction rather than by convention.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import PAYMENT_STATUS, PaymentStatus

if TYPE_CHECKING:
    from app.models.order import Order


class Payment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "payments"

    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[PaymentStatus] = mapped_column(PAYMENT_STATUS, nullable=False)

    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    # The exact string sent to the provider — the only reliable way to reconcile
    # against their dashboard later.
    description: Mapped[str] = mapped_column(String, nullable=False)

    card_brand: Mapped[str | None] = mapped_column(String, nullable=True)
    card_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)

    provider: Mapped[str] = mapped_column(String, nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String, nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String, nullable=True)

    order: Mapped["Order"] = relationship(back_populates="payments", lazy="raise")

    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="amount_positive"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
        CheckConstraint("card_last4 ~ '^[0-9]{4}$'", name="card_last4_format"),
        CheckConstraint(
            "status <> 'failed' OR failure_reason IS NOT NULL",
            name="failed_requires_reason",
        ),
        Index("ix_payments_order_id", "order_id"),
        # The database itself refuses a double charge.
        Index(
            "uq_payments_order_succeeded",
            "order_id",
            unique=True,
            postgresql_where=text("status = 'succeeded'"),
        ),
        Index(
            "uq_payments_provider_reference",
            "provider",
            "provider_reference",
            unique=True,
            postgresql_where=text("provider_reference IS NOT NULL"),
        ),
    )
