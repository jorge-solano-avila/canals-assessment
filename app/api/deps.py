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
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.event_log import LoggingEventPublisher
from app.adapters.geocoding_mock import MockGeocoder
from app.adapters.payment_mock import MockPaymentProvider
from app.config import settings
from app.db.session import Session
from app.ports.events import EventPublisher
from app.ports.geocoding import GeocodingProvider
from app.ports.payment import PaymentProvider
from app.services.order_checkout import OrderCheckoutService
from app.services.order_creation import OrderCreationService
from app.services.warehouse_selection import WarehouseSelectionService


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield one session per request. No transaction is started here."""
    async with Session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_geocoder() -> GeocodingProvider:
    """The mock geocoding provider.

    The only place a concrete geocoder is named; services depend on the port.
    Swapping in a real provider is a change to this function, and that is when a
    cache would start to earn its keep — with an in-process mock there is
    nothing to cache.
    """
    return MockGeocoder()


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


# Module-level singletons: the mocks are stateless apart from their recording
# lists, and a per-request instance would forget what it had charged — which is
# exactly what the "charged exactly once" test needs to observe.
_payment_provider = MockPaymentProvider()
_event_publisher = LoggingEventPublisher()


async def get_payment_provider() -> PaymentProvider:
    """The only place the concrete provider is named.

    Swapping in a real gateway is a change to this function and nothing else;
    services depend on the port.
    """
    return _payment_provider


PaymentDep = Annotated[PaymentProvider, Depends(get_payment_provider)]


async def get_event_publisher() -> EventPublisher:
    return _event_publisher


PublisherDep = Annotated[EventPublisher, Depends(get_event_publisher)]


async def get_checkout_service(
    session: SessionDep,
    geocoder: GeocoderDep,
    payments: PaymentDep,
) -> OrderCheckoutService:
    return OrderCheckoutService(
        session=session, geocoder=geocoder, payments=payments
    )


CheckoutDep = Annotated[OrderCheckoutService, Depends(get_checkout_service)]
