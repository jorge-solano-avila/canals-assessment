"""Test fixtures.

MIGRATIONS, NEVER create_all. A session-scoped fixture runs `alembic upgrade
head` against db-test. If the chain does not apply, the fixture raises and the
whole suite fails — a broken migration cannot be papered over by the tests.

TRUNCATE BETWEEN TESTS, NOT A SHARED ROLLED-BACK TRANSACTION. The common
"wrap each test in a transaction and roll it back" fixture pins the test to ONE
connection, and uncommitted rows are invisible to every other connection. Phase
4's concurrency tests — N racing reservations of the last unit — need genuinely
separate connections seeing genuinely committed data, which that fixture makes
impossible. Truncating now avoids rewriting the fixture layer one phase later.
"""

import subprocess
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings
from app.db.base import Base
from app.db.geo import to_point
from app.domain.geo import Coordinates
from app.domain.selection import RequestedItem
from app.models import Inventory, Product, Warehouse

TEST_URL = settings.test_database_url


@pytest.fixture(scope="session")
def _migrated() -> None:
    """Apply the Alembic chain to db-test. Suite fails if it does not apply."""
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        env={
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "POSTGRES_HOST": settings.postgres_test_host,
            "POSTGRES_DB": settings.postgres_test_db,
            "POSTGRES_USER": settings.postgres_user,
            "POSTGRES_PASSWORD": settings.postgres_password,
            "POSTGRES_PORT": str(settings.postgres_port),
        },
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "alembic upgrade head failed against the test database:\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


@pytest.fixture
async def engine(_migrated: None) -> AsyncIterator[AsyncEngine]:
    """Function-scoped with NullPool, deliberately.

    asyncpg binds a connection to the event loop that created it, and each test
    runs in its own loop. A session-scoped engine would hand loop-bound
    connections to a different loop and fail with "attached to a different
    loop". Creating the engine per test costs a connection handshake and removes
    a whole category of flakiness.
    """
    eng = create_async_engine(TEST_URL, poolclass=NullPool)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session per test, on its own connection, with a clean database.

    Truncation happens before the test rather than after, so a failed test
    leaves its data behind for inspection.
    """
    maker = async_sessionmaker(engine, expire_on_commit=False)
    tables = ", ".join(
        f'"{t.name}"' for t in Base.metadata.sorted_tables if t.name != "alembic_version"
    )
    async with maker() as s:
        await s.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
        await s.commit()
        yield s


# --------------------------------------------------------------------- builders
# Fixtures build data through the models, so tests exercise the same mapping the
# service uses rather than a parallel set of raw INSERTs.


async def make_product(session: AsyncSession, sku: str, price: int = 1000) -> Product:
    product = Product(
        sku=sku, name=f"Product {sku}", unit_price_minor=price, currency="EUR"
    )
    session.add(product)
    await session.flush()
    return product


async def make_warehouse(
    session: AsyncSession,
    code: str,
    *,
    at: Coordinates,
    is_active: bool = True,
) -> Warehouse:
    """Takes a Coordinates rather than two floats, for the same reason to_point
    is keyword-only: a transposed pair is silently valid."""
    warehouse = Warehouse(
        code=code,
        name=f"Warehouse {code}",
        location=to_point(lon=at.lon, lat=at.lat),
        is_active=is_active,
    )
    session.add(warehouse)
    await session.flush()
    return warehouse


async def make_stock(
    session: AsyncSession,
    warehouse: Warehouse,
    product: Product,
    *,
    on_hand: int,
    reserved: int = 0,
) -> Inventory:
    row = Inventory(
        warehouse_id=warehouse.id,
        product_id=product.id,
        on_hand=on_hand,
        reserved=reserved,
    )
    session.add(row)
    await session.flush()
    return row


def items(*pairs: tuple[UUID, int]) -> list[RequestedItem]:
    return [RequestedItem(product_id=pid, quantity=qty) for pid, qty in pairs]
