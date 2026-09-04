"""Idempotency status column

Restores the three states CLAUDE.md specifies — in_progress / completed / failed
— which phase 2 had replaced with state derived from `response_status IS NULL`.
The derived model could express "unsettled" and "settled", but not the
distinction between a settled failure that may be retried and one that must be
replayed. `failed -> retry allowed` needs a stored state.

Additive and reversible: a new enum type, a new NOT NULL column with a default
so existing rows are valid, one CHECK, and the sweeper's partial index re-pointed
at the new column.

payment_status is deliberately NOT altered. The `unknown` payment outcome is
represented by the existing `pending` value: a payment row is inserted pending
before the charge and only leaves that state when the provider answers, so a
timeout simply leaves it pending with failure_reason recording why.

Revision ID: 0003
Revises: 0002
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

IDEMPOTENCY_STATUS = ("in_progress", "completed", "failed")


def upgrade() -> None:
    rendered = ", ".join(f"'{v}'" for v in IDEMPOTENCY_STATUS)
    op.execute(f"CREATE TYPE idempotency_status AS ENUM ({rendered})")

    # server_default keeps existing rows valid under NOT NULL. It also matches
    # the insert path: a key is created in_progress and moves on from there.
    op.add_column(
        "idempotency_keys",
        sa.Column(
            "status",
            sa.Enum(*IDEMPOTENCY_STATUS, name="idempotency_status", create_type=False),
            server_default=sa.text("'in_progress'"),
            nullable=False,
        ),
    )

    # A completed key must carry the response it promises to replay.
    op.create_check_constraint(
        "completed_has_response",
        "idempotency_keys",
        "status <> 'completed' OR "
        "(response_body IS NOT NULL AND response_status IS NOT NULL)",
    )

    # The sweeper now keys on status rather than on the response columns.
    op.drop_index("ix_idempotency_keys_unsettled", table_name="idempotency_keys")
    op.create_index(
        "ix_idempotency_keys_in_progress",
        "idempotency_keys",
        ["created_at"],
        postgresql_where=sa.text("status = 'in_progress'"),
    )


def downgrade() -> None:
    op.drop_index("ix_idempotency_keys_in_progress", table_name="idempotency_keys")
    op.create_index(
        "ix_idempotency_keys_unsettled",
        "idempotency_keys",
        ["created_at"],
        postgresql_where=sa.text("response_status IS NULL"),
    )
    # SHORT name, not the fully-qualified one. Alembic applies
    # target_metadata's naming convention (ck_%(table_name)s_%(constraint_name)s)
    # to op.* calls, so passing the qualified name here produced
    # ck_idempotency_keys_ck_idempotency_keys_completed_has_response — the same
    # doubling that bit migration 0002 in phase 1.
    op.drop_constraint(
        "completed_has_response",
        "idempotency_keys",
        type_="check",
    )
    op.drop_column("idempotency_keys", "status")
    op.execute("DROP TYPE IF EXISTS idempotency_status")
