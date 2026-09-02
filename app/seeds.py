"""Idempotent seed data — safe to run repeatedly.

With no management APIs in the brief, this script is the *only* way reference data
enters the database, so it is load-bearing rather than a convenience.

The inventory matrix is arranged so warehouse selection is actually exercised:
  - SKU-MON27 is stocked in exactly ONE warehouse (SVQ-01).
  - SVQ-01 stocks ALL five products but is the farthest from Madrid and Barcelona.

So {KEYB, MOUSE} shipped to Madrid picks MAD-01 (nearest complete warehouse), while
adding MON27 forces the answer to SVQ-01 — completeness beating distance.

Re-running never overwrites `reserved`. Only `on_hand` is updated on conflict, so
re-seeding a database that has live reservations cannot corrupt the
on_hand >= reserved invariant.
"""

import asyncio
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.geo import to_point
from app.db.session import Session, engine
from app.models import Address, Customer, Inventory, Product, Warehouse

EUR = "EUR"

PRODUCTS = [
    # sku, name, unit_price_minor (cents)
    ("SKU-KEYB", "Mechanical Keyboard", 8900),
    ("SKU-MOUSE", "Wireless Mouse", 3450),
    ("SKU-MON27", '27" 4K Monitor', 42900),
    ("SKU-DOCK", "USB-C Docking Station", 15900),
    ("SKU-CABLE", "USB-C Cable 2m", 1290),
]

WAREHOUSES = [
    # code, name, longitude, latitude
    ("MAD-01", "Madrid Central", -3.7038, 40.4168),
    ("BCN-01", "Barcelona Port", 2.1734, 41.3851),
    ("VLC-01", "Valencia Sur", -0.3763, 39.4699),
    ("SVQ-01", "Seville Hub", -5.9845, 37.3891),
]

# (warehouse_code, sku, on_hand, reserved_on_insert)
INVENTORY = [
    ("MAD-01", "SKU-KEYB", 50, 5),  # reserved > 0 so the invariant is non-trivial
    ("MAD-01", "SKU-MOUSE", 40, 0),
    ("MAD-01", "SKU-DOCK", 12, 0),
    ("MAD-01", "SKU-CABLE", 100, 0),
    ("BCN-01", "SKU-KEYB", 30, 0),
    ("BCN-01", "SKU-MOUSE", 20, 0),
    ("BCN-01", "SKU-CABLE", 60, 0),
    ("VLC-01", "SKU-KEYB", 5, 0),
    ("VLC-01", "SKU-CABLE", 10, 0),
    ("SVQ-01", "SKU-KEYB", 10, 0),
    ("SVQ-01", "SKU-MOUSE", 10, 0),
    ("SVQ-01", "SKU-MON27", 3, 0),  # the only warehouse stocking MON27
    ("SVQ-01", "SKU-DOCK", 4, 0),
    ("SVQ-01", "SKU-CABLE", 25, 0),
]

CUSTOMER_EMAIL = "ada@example.com"
CUSTOMER_NAME = "Ada Lovelace"

ADDRESSES = [
    # line1, city, postal_code, country, longitude, latitude
    ("Calle de Alcala 45", "Madrid", "28014", "ES", -3.6975, 40.4189),
    ("Carrer de Mallorca 401", "Barcelona", "08013", "ES", 2.1744, 41.4036),
]


async def seed_products(session: AsyncSession) -> dict[str, UUID]:
    stmt = insert(Product).values(
        [
            {
                "sku": sku,
                "name": name,
                "unit_price_minor": price,
                "currency": EUR,
            }
            for sku, name, price in PRODUCTS
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["sku"],
        set_={
            "name": stmt.excluded["name"],
            "unit_price_minor": stmt.excluded["unit_price_minor"],
            "currency": stmt.excluded["currency"],
        },
    )
    await session.execute(stmt)

    rows = (await session.execute(select(Product.sku, Product.id))).all()
    return {sku: pid for sku, pid in rows}


async def seed_warehouses(session: AsyncSession) -> dict[str, UUID]:
    stmt = insert(Warehouse).values(
        [
            {"code": code, "name": name, "location": to_point(lon=lon, lat=lat)}
            for code, name, lon, lat in WAREHOUSES
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["code"],
        set_={
            "name": stmt.excluded["name"],
            "location": stmt.excluded["location"],
        },
    )
    await session.execute(stmt)

    rows = (await session.execute(select(Warehouse.code, Warehouse.id))).all()
    return {code: wid for code, wid in rows}


async def seed_inventory(
    session: AsyncSession,
    products: dict[str, UUID],
    warehouses: dict[str, UUID],
) -> None:
    stmt = insert(Inventory).values(
        [
            {
                "warehouse_id": warehouses[code],
                "product_id": products[sku],
                "on_hand": on_hand,
                "reserved": reserved,
            }
            for code, sku, on_hand, reserved in INVENTORY
        ]
    )
    # `reserved` is written on insert and never touched again — clobbering it
    # would break the on_hand >= reserved invariant for live reservations.
    stmt = stmt.on_conflict_do_update(
        constraint="uq_inventory_warehouse_product",
        set_={"on_hand": stmt.excluded["on_hand"]},
    )
    await session.execute(stmt)


async def seed_customer(session: AsyncSession) -> Customer:
    """No natural unique constraint to upsert against (uniqueness is a functional
    index on lower(email)), so select-then-insert."""
    existing = await session.scalar(
        select(Customer).where(func.lower(Customer.email) == CUSTOMER_EMAIL)
    )
    if existing is not None:
        return existing

    customer = Customer(email=CUSTOMER_EMAIL, full_name=CUSTOMER_NAME)
    session.add(customer)
    await session.flush()
    return customer


async def seed_addresses(session: AsyncSession, customer: Customer) -> None:
    for line1, city, postal, country, lon, lat in ADDRESSES:
        exists = await session.scalar(
            select(Address.id).where(
                Address.customer_id == customer.id,
                Address.line1 == line1,
            )
        )
        if exists is not None:
            continue
        session.add(
            Address(
                customer_id=customer.id,
                line1=line1,
                city=city,
                postal_code=postal,
                country_code=country,
                point=to_point(lon=lon, lat=lat),
            )
        )


async def main() -> None:
    async with Session() as session:
        async with session.begin():
            products = await seed_products(session)
            warehouses = await seed_warehouses(session)
            await seed_inventory(session, products, warehouses)
            customer = await seed_customer(session)
            await seed_addresses(session, customer)

    await engine.dispose()

    print(
        f"Seeded {len(PRODUCTS)} products, {len(WAREHOUSES)} warehouses, "
        f"{len(INVENTORY)} inventory rows, 1 customer, {len(ADDRESSES)} addresses."
    )


if __name__ == "__main__":
    asyncio.run(main())
