import asyncio
import logging
from typing import cast

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text

from distribook.api.deps import EngineDep

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)

READINESS_TIMEOUT_S = 1.0


@router.get("/healthz")
async def liveness() -> dict[str, str]:
    """The process is up and serving. Deliberately does not touch dependencies."""
    return {"status": "ok"}


@router.get("/readyz", responses={503: {"description": "The database is unreachable"}})
async def readiness(request: Request, engine: EngineDep) -> JSONResponse:
    """Ready to take traffic when the database answers within a short deadline.

    Redis is reported but never makes the service unready: it is an optimisation with a
    database-only fallback (ADR-0003), so taking replicas out of rotation when Redis is down
    would turn a degraded optimisation into an outage.
    """
    body: dict[str, str] = {"status": "ok", "database": "up"}
    try:
        async with asyncio.timeout(READINESS_TIMEOUT_S), engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # any failure means "not ready"; the reason is logged
        logger.warning("readiness check failed: %r", exc)
        body.update(status="unavailable", database="down")

    redis = cast(Redis | None, request.app.state.redis)
    if redis is not None:
        try:
            async with asyncio.timeout(READINESS_TIMEOUT_S):
                await redis.ping()
            body["redis"] = "up"
        except Exception as exc:
            logger.warning("redis ping failed: %r", exc)
            body["redis"] = "down"

    return JSONResponse(body, 200 if body["database"] == "up" else 503)
