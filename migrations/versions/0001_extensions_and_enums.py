"""Extensions and enum types

Kept separate from the schema migration so that "PostGIS before any table" is a
structural fact of the migration chain rather than a comment inside a large file.

Revision ID: 0001
Revises:
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ORDER_STATUS = (
    "pending",
    "reserved",
    "paid",
    "confirmed",
    "payment_failed",
    "cancelled",
)
PAYMENT_STATUS = ("pending", "succeeded", "failed")
RESERVATION_STATUS = ("active", "committed", "released", "expired")


def _create_enum(name: str, values: Sequence[str]) -> None:
    rendered = ", ".join(f"'{v}'" for v in values)
    op.execute(f"CREATE TYPE {name} AS ENUM ({rendered})")


def upgrade() -> None:
    # First statement of the whole chain, before any table exists.
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    _create_enum("order_status", ORDER_STATUS)
    _create_enum("payment_status", PAYMENT_STATUS)
    _create_enum("reservation_status", RESERVATION_STATUS)


def downgrade() -> None:
    op.execute("DROP TYPE IF EXISTS reservation_status")
    op.execute("DROP TYPE IF EXISTS payment_status")
    op.execute("DROP TYPE IF EXISTS order_status")

    # The extension is deliberately NOT dropped.
    #
    # The postgis/postgis image installs postgis (and postgis_topology, which
    # depends on it) into the database before any migration runs, so the
    # CREATE EXTENSION above is a no-op there — this migration never created it.
    # Dropping it would need CASCADE and would take postgis_topology with it:
    # destroying objects this migration does not own, to undo something it did
    # not do. CREATE EXTENSION IF NOT EXISTS is idempotent, so leaving it
    # installed costs nothing on the way back up.
