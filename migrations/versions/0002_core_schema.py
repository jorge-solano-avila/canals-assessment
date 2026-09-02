"""Core schema — all twelve tables

Hand-written rather than left as raw autogenerate output: the CHECK constraints,
the partial indexes and the GiST indexes are all spelled out here on purpose, and
constraint names are fully qualified so what lands in the database is exactly what
this file says.

Enum columns use create_type=False — the types were created in 0001.

Revision ID: 0002
Revises: 0001
"""

import uuid
from datetime import datetime
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geography
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _enum(name: str) -> postgresql.ENUM:
    return postgresql.ENUM(name=name, create_type=False)


def _point() -> Geography:
    # spatial_index=False: GeoAlchemy2 would otherwise create a GiST index as an
    # invisible side effect of create_table, colliding with the explicit ones below.
    return Geography(geometry_type="POINT", srid=4326, spatial_index=False)


def _created_at() -> sa.Column[datetime]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def _updated_at() -> sa.Column[datetime]:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


def _id() -> sa.Column[uuid.UUID]:
    return sa.Column(
        "id",
        postgresql.UUID(as_uuid=True),
        server_default=sa.text("gen_random_uuid()"),
        nullable=False,
    )


def upgrade() -> None:
    # ------------------------------------------------------------------ customers
    op.create_table(
        "customers",
        _id(),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("full_name", sa.String(), nullable=False),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_customers"),
    )
    # Case-insensitive uniqueness without the citext extension.
    op.execute(
        "CREATE UNIQUE INDEX ix_customers_email_lower ON customers (lower(email))"
    )

    # ------------------------------------------------------------------ products
    op.create_table(
        "products",
        _id(),
        sa.Column("sku", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("unit_price_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_products"),
        sa.UniqueConstraint("sku", name="uq_products_sku"),
        sa.CheckConstraint(
            "unit_price_minor >= 0", name="unit_price_non_negative"
        ),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
    )

    # ---------------------------------------------------------------- warehouses
    op.create_table(
        "warehouses",
        _id(),
        sa.Column("code", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("location", _point(), nullable=False),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_warehouses"),
        sa.UniqueConstraint("code", name="uq_warehouses_code"),
    )
    # Explicit GiST index — the one warehouse selection actually uses.
    op.create_index(
        "ix_warehouses_location", "warehouses", ["location"], postgresql_using="gist"
    )

    # ----------------------------------------------------------------- addresses
    op.create_table(
        "addresses",
        _id(),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("line1", sa.String(), nullable=False),
        sa.Column("line2", sa.String(), nullable=True),
        sa.Column("city", sa.String(), nullable=False),
        sa.Column("postal_code", sa.String(), nullable=False),
        sa.Column("country_code", sa.String(length=2), nullable=False),
        sa.Column("point", _point(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_addresses"),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_addresses_customer_id_customers",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "country_code ~ '^[A-Z]{2}$'", name="country_code_format"
        ),
    )
    op.create_index("ix_addresses_customer_id", "addresses", ["customer_id"])
    op.create_index(
        "ix_addresses_point",
        "addresses",
        ["point"],
        postgresql_using="gist",
        postgresql_where=sa.text("point IS NOT NULL"),
    )

    # ----------------------------------------------------------------- inventory
    op.create_table(
        "inventory",
        _id(),
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("on_hand", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reserved", sa.Integer(), server_default="0", nullable=False),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_inventory"),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            ["warehouses.id"],
            name="fk_inventory_warehouse_id_warehouses",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_inventory_product_id_products",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "warehouse_id", "product_id", name="uq_inventory_warehouse_product"
        ),
        sa.CheckConstraint("reserved >= 0", name="reserved_non_negative"),
        sa.CheckConstraint(
            "on_hand >= reserved", name="on_hand_ge_reserved"
        ),
    )
    # The unique constraint's index leads with warehouse_id and cannot serve
    # warehouse selection, which filters by product_id IN (...) first.
    op.create_index(
        "ix_inventory_product_warehouse", "inventory", ["product_id", "warehouse_id"]
    )

    # -------------------------------------------------------------------- orders
    op.create_table(
        "orders",
        _id(),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            _enum("order_status"),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("total_minor", sa.BigInteger(), nullable=False),
        sa.Column("shipping_line1", sa.String(), nullable=False),
        sa.Column("shipping_line2", sa.String(), nullable=True),
        sa.Column("shipping_city", sa.String(), nullable=False),
        sa.Column("shipping_postal_code", sa.String(), nullable=False),
        sa.Column("shipping_country_code", sa.String(length=2), nullable=False),
        sa.Column("shipping_point", _point(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_orders"),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name="fk_orders_customer_id_customers",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            ["warehouses.id"],
            name="fk_orders_warehouse_id_warehouses",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
        sa.CheckConstraint(
            "shipping_country_code ~ '^[A-Z]{2}$'",
            name="shipping_country_format",
        ),
        sa.CheckConstraint("total_minor >= 0", name="total_non_negative"),
        # Any state at or past 'reserved' must name both the warehouse the stock
        # came from and the geocoded point that chose it.
        sa.CheckConstraint(
            "status IN ('pending', 'cancelled') "
            "OR (warehouse_id IS NOT NULL AND shipping_point IS NOT NULL)",
            name="reserved_requires_warehouse_and_point",
        ),
    )
    op.execute(
        "CREATE INDEX ix_orders_customer_created "
        "ON orders (customer_id, created_at DESC)"
    )
    op.create_index(
        "ix_orders_open",
        "orders",
        ["status", "created_at"],
        postgresql_where=sa.text("status IN ('pending', 'reserved')"),
    )
    # No GiST index on shipping_point: it is the *query* point that ordering uses,
    # not indexed data. It would be paid for on every insert and never read.

    # --------------------------------------------------------------- order_items
    op.create_table(
        "order_items",
        _id(),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_sku", sa.String(), nullable=False),
        sa.Column("product_name", sa.String(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "line_total_minor",
            sa.BigInteger(),
            sa.Computed("quantity * unit_price_minor", persisted=True),
            nullable=False,
        ),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_order_items"),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_order_items_order_id_orders",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_order_items_product_id_products",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "order_id", "product_id", name="uq_order_items_order_product"
        ),
        sa.CheckConstraint("quantity > 0", name="quantity_positive"),
        sa.CheckConstraint(
            "unit_price_minor >= 0", name="unit_price_non_negative"
        ),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'", name="currency_format"
        ),
    )

    # ------------------------------------------------------ order_status_history
    op.create_table(
        "order_status_history",
        _id(),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("from_status", _enum("order_status"), nullable=True),
        sa.Column("to_status", _enum("order_status"), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_order_status_history"),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_order_status_history_order_id_orders",
            ondelete="CASCADE",
        ),
        # IS DISTINCT FROM rather than <> so the NULL creation row passes.
        sa.CheckConstraint(
            "from_status IS DISTINCT FROM to_status",
            name="transition_changes_status",
        ),
    )
    op.create_index(
        "ix_order_status_history_order",
        "order_status_history",
        ["order_id", "occurred_at"],
    )

    # ------------------------------------------------------------------ payments
    op.create_table(
        "payments",
        _id(),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", _enum("payment_status"), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("card_brand", sa.String(), nullable=True),
        sa.Column("card_last4", sa.String(length=4), nullable=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("provider_reference", sa.String(), nullable=True),
        sa.Column("failure_reason", sa.String(), nullable=True),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_payments"),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_payments_order_id_orders",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("amount_minor > 0", name="amount_positive"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="currency_format"),
        sa.CheckConstraint(
            "card_last4 ~ '^[0-9]{4}$'", name="card_last4_format"
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR failure_reason IS NOT NULL",
            name="failed_requires_reason",
        ),
    )
    op.create_index("ix_payments_order_id", "payments", ["order_id"])
    # The database itself refuses a double charge.
    op.create_index(
        "uq_payments_order_succeeded",
        "payments",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("status = 'succeeded'"),
    )
    op.create_index(
        "uq_payments_provider_reference",
        "payments",
        ["provider", "provider_reference"],
        unique=True,
        postgresql_where=sa.text("provider_reference IS NOT NULL"),
    )

    # -------------------------------------------------------- stock_reservations
    op.create_table(
        "stock_reservations",
        _id(),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("warehouse_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            _enum("reservation_status"),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.PrimaryKeyConstraint("id", name="pk_stock_reservations"),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_stock_reservations_order_id_orders",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["warehouse_id"],
            ["warehouses.id"],
            name="fk_stock_reservations_warehouse_id_warehouses",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_stock_reservations_product_id_products",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "order_id",
            "warehouse_id",
            "product_id",
            name="uq_stock_reservations_order_warehouse_product",
        ),
        sa.CheckConstraint("quantity > 0", name="quantity_positive"),
        sa.CheckConstraint(
            "status = 'active' OR released_at IS NOT NULL",
            name="settled_requires_released_at",
        ),
    )
    # Matches the sweeper's predicate exactly; settled rows drop out of it.
    op.create_index(
        "ix_stock_reservations_sweeper",
        "stock_reservations",
        ["expires_at"],
        postgresql_where=sa.text("status = 'active'"),
    )

    # ---------------------------------------------------------- idempotency_keys
    op.create_table(
        "idempotency_keys",
        _id(),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("response_body", postgresql.JSONB(), nullable=True),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=True),
        _created_at(),
        _updated_at(),
        sa.PrimaryKeyConstraint("id", name="pk_idempotency_keys"),
        sa.UniqueConstraint("key", name="uq_idempotency_keys_key"),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name="fk_idempotency_keys_order_id_orders",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "char_length(request_hash) = 64",
            name="request_hash_sha256",
        ),
        sa.CheckConstraint(
            "response_status BETWEEN 100 AND 599",
            name="response_status_range",
        ),
        # No status column: state is derived. These two columns are written
        # together or not at all, which is what makes that derivation trustworthy.
        sa.CheckConstraint(
            "(response_body IS NULL) = (response_status IS NULL)",
            name="response_columns_paired",
        ),
    )
    op.create_index("ix_idempotency_keys_created_at", "idempotency_keys", ["created_at"])
    op.create_index(
        "ix_idempotency_keys_unsettled",
        "idempotency_keys",
        ["created_at"],
        postgresql_where=sa.text("response_status IS NULL"),
    )

    # -------------------------------------------------------------------- outbox
    # Deliberately no foreign key: an append-only log must stay publishable even
    # if its aggregate is gone.
    op.create_table(
        "outbox",
        _id(),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column(
            "aggregate_type",
            sa.String(),
            server_default=sa.text("'order'"),
            nullable=False,
        ),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        _created_at(),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_outbox"),
        sa.CheckConstraint("attempts >= 0", name="attempts_non_negative"),
    )
    op.create_index(
        "ix_outbox_unprocessed",
        "outbox",
        ["created_at"],
        postgresql_where=sa.text("processed_at IS NULL"),
    )


def downgrade() -> None:
    # Reverse dependency order.
    op.drop_table("outbox")
    op.drop_table("idempotency_keys")
    op.drop_table("stock_reservations")
    op.drop_table("payments")
    op.drop_table("order_status_history")
    op.drop_table("order_items")
    op.drop_table("orders")
    op.drop_table("inventory")
    op.drop_table("addresses")
    op.drop_table("warehouses")
    op.drop_table("products")
    op.drop_table("customers")
