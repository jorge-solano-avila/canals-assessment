"""The ASGI application.

Wiring only: routers, exception handlers, middleware and logging. No business
logic lives here, and no route touches an adapter directly — everything arrives
through Depends, which is the one place ports are bound to implementations.
"""

import logging

from fastapi import FastAPI

from app.api.errors import register_exception_handlers
from app.api.logging import configure_logging
from app.api.middleware import correlation_id_middleware
from app.api.routes import health, orders
from app.config import settings

configure_logging(logging.DEBUG if settings.app_env == "local" else logging.INFO)

app = FastAPI(
    title="Canals Order Service",
    version="0.5.0",
    description="Creates orders: selects a warehouse, reserves stock, charges a payment provider.",
)

app.middleware("http")(correlation_id_middleware)
register_exception_handlers(app)
app.include_router(orders.router)
app.include_router(health.router)
