"""POST /orders — the endpoint the whole project exists for."""

import logging
from typing import Annotated

from fastapi import APIRouter, Header, Request, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import CheckoutDep
from app.domain.address import PostalAddress
from app.domain.errors import IdempotencyKeyRequired
from app.domain.selection import RequestedItem
from app.repositories.idempotency import request_hash
from app.schemas.common import ErrorResponse
from app.schemas.order import CreateOrderRequest, OrderResponse
from app.services.order_checkout import ReplayedResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["orders"])


@router.post(
    "/orders",
    status_code=status.HTTP_201_CREATED,
    response_model=OrderResponse,
    responses={
        400: {"model": ErrorResponse},
        402: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def create_order(
    request: Request,
    response: Response,
    payload: CreateOrderRequest,
    checkout: CheckoutDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    """Create an order: select a warehouse, reserve stock, charge, confirm.

    The Idempotency-Key header is required rather than optional: this endpoint
    moves money, and a client that cannot retry safely will retry unsafely.
    """
    if not idempotency_key or not idempotency_key.strip():
        raise IdempotencyKeyRequired()

    # Hashed from the serialised schema, which excludes the card number — so the
    # hash cannot be reversed into a PAN even if it leaked.
    hashed = request_hash(payload.model_dump(mode="json"))

    address = PostalAddress(
        line1=payload.shipping_address.line1,
        line2=payload.shipping_address.line2,
        city=payload.shipping_address.city,
        postal_code=payload.shipping_address.postal_code,
        country_code=payload.shipping_address.country_code,
    )
    items = [
        RequestedItem(product_id=i.product_id, quantity=i.quantity)
        for i in payload.items
    ]

    try:
        result = await checkout.checkout(
            idempotency_key=idempotency_key,
            request_hash=hashed,
            customer_id=payload.customer_id,
            address=address,
            items=items,
            card=payload.card_number,
        )
    except ReplayedResponse as replay:
        # A completed key returns its stored response verbatim, with no
        # re-charge and no new order.
        return JSONResponse(
            status_code=replay.status_code,
            content=replay.body,
            headers={"Location": f"/orders/{replay.body.get('id', '')}"},
        )

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content=result.body,
        headers={"Location": f"/orders/{result.order_id}"},
    )
