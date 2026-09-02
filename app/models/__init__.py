"""Re-export every model so Base.metadata is complete.

Alembic's env.py imports this module: a model that is not imported here is invisible
to autogenerate, and its table would be silently proposed for deletion.
"""

from app.db.base import Base
from app.models.address import Address
from app.models.customer import Customer
from app.models.enums import (
    OrderStatus,
    PaymentStatus,
    ReservationStatus,
)
from app.models.idempotency import IdempotencyKey
from app.models.inventory import Inventory
from app.models.order import Order
from app.models.order_item import OrderItem
from app.models.order_status_history import OrderStatusHistory
from app.models.outbox import OutboxEvent
from app.models.payment import Payment
from app.models.product import Product
from app.models.reservation import StockReservation
from app.models.warehouse import Warehouse

__all__ = [
    "Base",
    "Address",
    "Customer",
    "IdempotencyKey",
    "Inventory",
    "Order",
    "OrderItem",
    "OrderStatus",
    "OrderStatusHistory",
    "OutboxEvent",
    "Payment",
    "PaymentStatus",
    "Product",
    "ReservationStatus",
    "StockReservation",
    "Warehouse",
]
