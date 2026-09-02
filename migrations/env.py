"""Alembic environment — async, wired to Base.metadata.

Two non-obvious settings, both load-bearing:

1. poolclass=NullPool. Migrations are a one-shot process; a pooled async engine
   leaves connections for the event loop to reap at interpreter shutdown, which
   surfaces as spurious "Event loop is closed" noise after a successful upgrade.

2. The connection pins search_path to `public`, and include_object excludes the
   PostGIS-owned objects that live there. The postgis/postgis image ships
   postgis_tiger_geocoder and postgis_topology and sets search_path to
   "$user", public, topology, tiger — so without the pin, autogenerate reflects
   34 tiger tables plus spatial_ref_sys as if they were ours and proposes
   dropping every one of them. That is how a destructive migration gets shipped
   by accident. postgis itself and the geography type live in public, so the pin
   costs nothing.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.models import Base  # noqa: F401  (imports every model into the metadata)

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Tables and views PostGIS creates and owns. Alembic must never manage these.
POSTGIS_OWNED = {"spatial_ref_sys", "geometry_columns", "geography_columns"}


def include_object(object_, name, type_, reflected, compare_to):
    if type_ == "table" and name in POSTGIS_OWNED:
        return False
    # GeoAlchemy2 reflects its own spatial indexes; we declare ours explicitly.
    if type_ == "index" and name is not None and name.startswith("idx_"):
        return False
    return True


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        # search_path is set at connection time, NOT with an execute("SET ...").
        # An execute() here auto-begins a transaction that alembic does not own,
        # and closing the connection then rolls the entire migration back — while
        # alembic still logs "Running upgrade" and exits 0. Silent no-op migrations
        # are far worse than a loud failure.
        connect_args={"server_settings": {"search_path": "public"}},
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
