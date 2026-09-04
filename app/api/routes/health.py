"""GET /health — dependency check.

PostgreSQL is the only external dependency, and the service cannot do anything
without it, so an unreachable database is a 503 rather than a degraded state.
The check itself is a plain `SELECT 1`: it proves the connection pool can hand
out a working connection, which is what a load balancer actually needs to know.
"""

from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.api.deps import SessionDep

router = APIRouter(tags=["ops"])


@router.get("/health")
async def health(response: Response, session: SessionDep) -> dict[str, Any]:
    try:
        await session.execute(text("SELECT 1"))
        checks = {"database": "ok"}
        overall = "ok"
    except Exception:
        checks = {"database": "unavailable"}
        overall = "unhealthy"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {"status": overall, "checks": checks}
