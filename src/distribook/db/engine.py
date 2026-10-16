from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from distribook.config import Settings


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
