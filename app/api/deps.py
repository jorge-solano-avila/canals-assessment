"""Request-scoped dependencies and adapter wiring.

This is the ONLY module that names concrete adapters. services/ depends on the
ports; api/ decides which implementation satisfies them.

TRANSACTION OWNERSHIP: get_session deliberately does NOT open a transaction.

CLAUDE.md mandates the sequence reserve -> commit -> charge -> commit, so the
payment call happens *between* two transactions. A request-scoped transaction
could not express that, and it would hold row locks on `inventory` open across an
external HTTP call to the payment provider. Transaction boundaries belong to the
services (phase 4+), which open tightly-scoped `async with session.begin():`
blocks around the database work and nothing else.

CLEANUP ON EXCEPTION is already correct without an explicit rollback. When a route
raises, the exception is thrown into this generator; `async with Session()` then
runs AsyncSession.__aexit__ -> close(), which releases the connection and rolls
back any uncommitted work. An `except: await session.rollback()` here would be
dead code implying the context manager cannot be trusted.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.geocoding_cache import CachedGeocoder
from app.adapters.geocoding_mock import MockGeocoder
from app.config import settings
from app.db.session import Session
from app.ports.geocoding import GeocodingProvider
from app.services.order_creation import OrderCreationService
from app.services.warehouse_selection import WarehouseSelectionService


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield one session per request. No transaction is started here."""
    async with Session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_redis() -> AsyncIterator[Redis]:
    """One Redis client per request.

    Constructed even when Redis is down: the client connects lazily and
    CachedGeocoder treats every failure as a cache miss, so an unreachable
    Redis never prevents a request from being served.
    """
    client: Redis = Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=0.25,
        socket_timeout=0.25,
    )
    try:
        yield client
    finally:
        await client.aclose()


RedisDep = Annotated[Redis, Depends(get_redis)]


async def get_geocoder(redis: RedisDep) -> GeocodingProvider:
    """The mock provider behind the Redis cache.

    Swapping the implementation is a change to this one function, or a
    dependency_overrides entry in a test.
    """
    return CachedGeocoder(
        inner=MockGeocoder(),
        redis=redis,
        ttl_seconds=settings.geocode_cache_ttl_seconds,
    )


GeocoderDep = Annotated[GeocodingProvider, Depends(get_geocoder)]


async def get_warehouse_selection_service(
    session: SessionDep,
    geocoder: GeocoderDep,
) -> WarehouseSelectionService:
    return WarehouseSelectionService(session=session, geocoder=geocoder)


WarehouseSelectionDep = Annotated[
    WarehouseSelectionService, Depends(get_warehouse_selection_service)
]


async def get_order_creation_service(
    session: SessionDep,
    geocoder: GeocoderDep,
) -> OrderCreationService:
    return OrderCreationService(session=session, geocoder=geocoder)


OrderCreationDep = Annotated[
    OrderCreationService, Depends(get_order_creation_service)
]
