import asyncio
import logging

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from distribook.api.deps import EngineDep

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)

READINESS_TIMEOUT_S = 1.0


@router.get("/healthz")
async def liveness() -> dict[str, str]:
    """The process is up and serving. Deliberately does not touch dependencies."""
    return {"status": "ok"}


@router.get("/readyz", responses={503: {"description": "A dependency is unreachable"}})
async def readiness(engine: EngineDep) -> JSONResponse:
    """Ready to take traffic: the database answers within a short deadline."""
    try:
        async with asyncio.timeout(READINESS_TIMEOUT_S), engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # any failure means "not ready"; the reason is logged
        logger.warning("readiness check failed: %r", exc)
        return JSONResponse({"status": "unavailable", "database": "down"}, 503)
    return JSONResponse({"status": "ok", "database": "up"})
