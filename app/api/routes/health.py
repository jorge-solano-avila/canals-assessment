"""GET /health — dependency checks, reported separately.

The two dependencies are NOT equal, and the response reflects that:

  Postgres unreachable -> 503. Nothing works without it.
  Redis unreachable    -> 200, degraded. Redis is only the geocoding cache, and
                          phase 3 proved (with a test) that the request path
                          survives without it. Returning 503 would pull a
                          working instance out of a load balancer for a
                          degradation that costs nothing but a cache miss.
"""

from typing import Any

from fastapi import APIRouter, Response, status
from redis.exceptions import RedisError
from sqlalchemy import text

from app.api.deps import RedisDep, SessionDep

router = APIRouter(tags=["ops"])


@router.get("/health")
async def health(
    response: Response,
    session: SessionDep,
    redis: RedisDep,
) -> dict[str, Any]:
    checks: dict[str, str] = {}

    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:
        checks["database"] = "unavailable"

    try:
        await redis.ping()
        checks["redis"] = "ok"
    except (RedisError, OSError, TimeoutError):
        checks["redis"] = "unavailable"

    if checks["database"] != "ok":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        overall = "unhealthy"
    elif checks["redis"] != "ok":
        overall = "degraded"
    else:
        overall = "ok"

    return {"status": overall, "checks": checks}
