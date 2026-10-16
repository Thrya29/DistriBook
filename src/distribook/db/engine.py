import asyncio
import logging
from contextlib import AsyncExitStack

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from distribook.config import Settings

logger = logging.getLogger(__name__)


def create_engine(settings: Settings) -> AsyncEngine:
    """Create the connection pool.

    ``max_overflow=0`` makes ``pool_size`` a hard ceiling, so the connection budget across
    replicas (replicas x pool_size) is predictable. When the pool is exhausted a request waits
    at most ``pool_timeout`` and is then shed with 503 instead of queueing without bound.
    """
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=0,
        pool_timeout=settings.db_pool_timeout_s,
        pool_pre_ping=True,
        pool_recycle=1800,
        connect_args={
            "timeout": settings.db_connect_timeout_s,
            "server_settings": settings.server_settings,
        },
    )


async def warm_pool(engine: AsyncEngine, size: int) -> int:
    """Open ``size`` connections up front and return them to the pool.

    Without this, the first burst after start-up has to open every connection at once while
    also serving traffic, and connection set-up (TCP, auth, session settings) competes with the
    requests that need it. Returns how many connections were opened; a database that is down at
    start-up is logged, not fatal, since readiness reports it and the pool retries later.
    """
    async with AsyncExitStack() as stack:
        results = await asyncio.gather(
            *(stack.enter_async_context(engine.connect()) for _ in range(size)),
            return_exceptions=True,
        )
        opened = [conn for conn in results if not isinstance(conn, BaseException)]
        for conn in opened:
            await conn.execute(text("SELECT 1"))
    failures = [r for r in results if isinstance(r, BaseException)]
    if failures:
        logger.warning(
            "connection pool warm-up incomplete",
            extra={"fields": {"opened": len(opened), "wanted": size, "error": repr(failures[0])}},
        )
    return len(opened)
