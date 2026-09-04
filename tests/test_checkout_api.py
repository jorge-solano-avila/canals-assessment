"""POST /orders end to end, against real PostgreSQL.

Idempotency, the payment saga and the compensating paths are all observable only
through the whole stack, so these drive the real ASGI app with a real database.
"""

import asyncio
import logging
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.geocoding_mock import MockGeocoder
from app.adapters.payment_mock import MockPaymentProvider
from app.api import deps
from app.domain.enums import IdempotencyStatus, OrderStatus, PaymentStatus, ReservationStatus
from app.domain.geo import Coordinates
from app.main import app
from app.models import IdempotencyKey, Inventory, Order, OutboxEvent, Payment, StockReservation
from tests.conftest import make_customer, make_product, make_stock, make_warehouse

MADRID = Coordinates(lat=40.4168, lon=-3.7038)

GOOD_CARD = "4242424242424242"      # succeeds
DECLINED_CARD = "4000000000000002"  # ...0002 -> declined
TIMEOUT_CARD = "4000000000000069"   # ...0069 -> unknown


def body(customer_id: UUID, product_id: UUID, card: str = GOOD_CARD, qty: int = 1) -> dict[str, Any]:
    return {
        "customer_id": str(customer_id),
        "shipping_address": {
            "line1": "Calle de Alcala 45",
            "city": "Madrid",
            "postal_code": "28014",
            "country_code": "ES",
        },
        "items": [{"product_id": str(product_id), "quantity": qty}],
        "card_number": card,
    }


@pytest.fixture
async def api(session: AsyncSession, session_factory: Any) -> Any:
    """The real app, pointed at db-test, with a fresh payment provider.

    The session dependency is overridden so routes use the test database; the
    payment provider is replaced per test so its recorded charge list starts
    empty and can be asserted on.
    """
    provider = MockPaymentProvider()

    async def _session_override() -> Any:
        async with session_factory() as s:
            yield s

    app.dependency_overrides[deps.get_session] = _session_override
    app.dependency_overrides[deps.get_payment_provider] = lambda: provider
    app.dependency_overrides[deps.get_geocoder] = lambda: MockGeocoder()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, provider

    app.dependency_overrides.clear()


@pytest.fixture
async def fixtures(session: AsyncSession) -> Any:
    warehouse = await make_warehouse(session, "MAD-01", at=MADRID)
    product = await make_product(session, "SKU-KEYB", price=8900)
    await make_stock(session, warehouse, product, on_hand=10)
    customer = await make_customer(session)
    await session.commit()
    return customer, product, warehouse


async def test_happy_path(api: Any, fixtures: Any, session: AsyncSession) -> None:
    """201, Location header, order confirmed, outbox row written."""
    client, provider = api
    customer, product, _ = fixtures

    r = await client.post(
        "/orders",
        json=body(customer.id, product.id, qty=2),
        headers={"Idempotency-Key": "key-happy"},
    )

    assert r.status_code == 201, r.text
    payload = r.json()
    assert r.headers["Location"] == f"/orders/{payload['id']}"
    assert payload["status"] == "confirmed"
    assert payload["total_minor"] == 17800
    assert payload["payment_reference"] is not None
    assert len(payload["items"]) == 1

    order = (await session.scalars(select(Order))).one()
    await session.refresh(order)
    assert order.id == UUID(payload["id"])
    assert order.status == OrderStatus.confirmed

    events = (await session.scalars(select(OutboxEvent))).all()
    assert [e.event_type for e in events] == ["order_confirmed"]
    assert events[0].processed_at is None  # the relay has not run yet

    reservations = (await session.scalars(select(StockReservation))).all()
    assert all(r.status == ReservationStatus.committed for r in reservations)
    assert provider.charged_keys == [str(order.id)]


async def test_same_key_concurrently_charges_once(
    api: Any, fixtures: Any, session: AsyncSession
) -> None:
    """The whole point of idempotency: one order, one charge."""
    client, provider = api
    customer, product, _ = fixtures
    payload = body(customer.id, product.id)

    responses = await asyncio.gather(
        *(
            client.post("/orders", json=payload, headers={"Idempotency-Key": "key-race"})
            for _ in range(5)
        )
    )
    codes = sorted(r.status_code for r in responses)

    assert codes.count(201) == 1, f"expected exactly one creation, got {codes}"
    assert all(c in (201, 409) for c in codes), codes
    assert len(provider.charged_keys) == 1, provider.charged_keys
    assert await session.scalar(select(func.count()).select_from(Order)) == 1


async def test_replay_after_completion_returns_identical_body(
    api: Any, fixtures: Any
) -> None:
    """A completed key replays verbatim and does not re-charge."""
    client, provider = api
    customer, product, _ = fixtures
    payload = body(customer.id, product.id)
    headers = {"Idempotency-Key": "key-replay"}

    first = await client.post("/orders", json=payload, headers=headers)
    assert first.status_code == 201
    charges_after_first = len(provider.charged_keys)

    second = await client.post("/orders", json=payload, headers=headers)

    assert second.status_code == 201
    assert second.json() == first.json(), "replay differed from the original"
    assert len(provider.charged_keys) == charges_after_first, "re-charged on replay"


async def test_same_key_different_body_is_422(api: Any, fixtures: Any) -> None:
    """The hash check precedes the state branch."""
    client, _ = api
    customer, product, _ = fixtures
    headers = {"Idempotency-Key": "key-mismatch"}

    first = await client.post("/orders", json=body(customer.id, product.id), headers=headers)
    assert first.status_code == 201

    second = await client.post(
        "/orders", json=body(customer.id, product.id, qty=3), headers=headers
    )

    assert second.status_code == 422
    assert second.json()["code"] == "idempotency_key_conflict"


async def test_missing_idempotency_key_is_400(api: Any, fixtures: Any) -> None:
    client, _ = api
    customer, product, _ = fixtures

    r = await client.post("/orders", json=body(customer.id, product.id))

    assert r.status_code == 400
    assert r.json()["code"] == "validation_error"


async def test_declined_releases_stock_and_marks_failed(
    api: Any, fixtures: Any, session: AsyncSession
) -> None:
    """The only outcome that compensates automatically."""
    client, _ = api
    customer, product, _ = fixtures

    before = (await session.execute(select(Inventory))).scalar_one()
    await session.refresh(before)
    original_reserved = before.reserved

    r = await client.post(
        "/orders",
        json=body(customer.id, product.id, card=DECLINED_CARD),
        headers={"Idempotency-Key": "key-declined"},
    )

    assert r.status_code == 402
    assert r.json()["code"] == "payment_declined"

    order = (await session.scalars(select(Order))).one()
    await session.refresh(order)
    assert order.status == OrderStatus.payment_failed

    payment = (await session.scalars(select(Payment))).one()
    await session.refresh(payment)
    assert payment.status == PaymentStatus.failed
    assert payment.card_last4 == "0002"

    row = (await session.execute(select(Inventory))).scalar_one()
    await session.refresh(row)
    assert row.reserved == original_reserved, "stock was not returned"

    key = (await session.scalars(select(IdempotencyKey))).one()
    await session.refresh(key)
    assert key.status == IdempotencyStatus.failed


async def test_timeout_keeps_the_hold(
    api: Any, fixtures: Any, session: AsyncSession
) -> None:
    """Unknown is not failure: stock stays held, nothing is confirmed."""
    client, _ = api
    customer, product, _ = fixtures

    r = await client.post(
        "/orders",
        json=body(customer.id, product.id, card=TIMEOUT_CARD),
        headers={"Idempotency-Key": "key-timeout"},
    )

    assert r.status_code == 502
    assert r.json()["code"] == "payment_unresolved"

    order = (await session.scalars(select(Order))).one()
    await session.refresh(order)
    assert order.status == OrderStatus.reserved, "confirmed or failed on an unknown outcome"

    payment = (await session.scalars(select(Payment))).one()
    await session.refresh(payment)
    assert payment.status == PaymentStatus.pending, "unknown must stay pending"
    assert payment.failure_reason == "provider timeout"

    reservation = (await session.scalars(select(StockReservation))).one()
    await session.refresh(reservation)
    assert reservation.status == ReservationStatus.active, "hold was released"

    row = (await session.execute(select(Inventory))).scalar_one()
    await session.refresh(row)
    assert row.reserved == 1, "stock must stay held while the outcome is unknown"

    key = (await session.scalars(select(IdempotencyKey))).one()
    await session.refresh(key)
    assert key.status == IdempotencyStatus.in_progress, "a retry could double-charge"


async def test_retry_after_failure_is_allowed(
    api: Any, fixtures: Any, session: AsyncSession
) -> None:
    """failed -> retry allowed, which is why the status column exists."""
    client, provider = api
    customer, product, _ = fixtures
    headers = {"Idempotency-Key": "key-retry"}

    declined = await client.post(
        "/orders", json=body(customer.id, product.id, card=DECLINED_CARD), headers=headers
    )
    assert declined.status_code == 402
    assert len(provider.charged_keys) == 1

    # Same key, same body, retried after the failure.
    retry = await client.post(
        "/orders", json=body(customer.id, product.id, card=DECLINED_CARD), headers=headers
    )

    assert retry.status_code == 402, "the retry was refused instead of re-attempted"
    assert len(provider.charged_keys) == 2, "the retry did not reach the provider"


async def test_card_number_never_reaches_the_logs(
    api: Any, fixtures: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Captures every record emitted during a real request."""
    client, _ = api
    customer, product, _ = fixtures

    with caplog.at_level(logging.DEBUG):
        r = await client.post(
            "/orders",
            json=body(customer.id, product.id),
            headers={"Idempotency-Key": "key-logs"},
        )
    assert r.status_code == 201

    haystack = "\n".join(
        [rec.getMessage() for rec in caplog.records]
        + [str(rec.args) for rec in caplog.records]
    )
    assert GOOD_CARD not in haystack, "the card number reached a log record"
    assert GOOD_CARD not in r.text, "the card number came back in the response"
    # The last four are fine and deliberately present in the charge log.
    assert "4242" in haystack


async def test_health_reports_dependencies_separately(api: Any) -> None:
    """Postgres is fatal; Redis is only degraded."""
    client, _ = api

    r = await client.get("/health")

    assert r.status_code == 200
    payload = r.json()
    assert payload["checks"]["database"] == "ok"
    # Redis is genuinely unreachable from the test container's settings or it is
    # up; either way the service is not 503 on its account.
    assert payload["status"] in {"ok", "degraded"}
    assert r.status_code != 503
