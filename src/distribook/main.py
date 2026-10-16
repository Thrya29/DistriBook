"""Application factory. Run with: ``uvicorn distribook.main:create_app --factory``."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff

from distribook import __version__
from distribook.api import bookings, events, health
from distribook.api.problems import register_problem_handlers
from distribook.config import Settings
from distribook.db.engine import create_engine, warm_pool
from distribook.observability.logging import configure_logging
from distribook.observability.metrics import Metrics
from distribook.observability.middleware import RequestContextMiddleware
from distribook.services.booking_service import BookingService
from distribook.services.catalog_service import CatalogService
from distribook.services.maintenance import purge_expired_idempotency_keys, run_periodically
from distribook.strategies import build_strategy

logger = logging.getLogger(__name__)


def _create_redis(settings: Settings) -> Redis:
    # No client-side retries: a struggling Redis is skipped (the strategy falls back to the
    # database-only path), never waited on.
    return Redis.from_url(
        settings.redis_url,
        socket_timeout=settings.redis_timeout_s,
        socket_connect_timeout=settings.redis_timeout_s,
        retry=Retry(NoBackoff(), 0),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level, json_format=settings.log_json)
    metrics = Metrics()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        engine = create_engine(settings)
        metrics.bind_pool(engine)
        if settings.db_pool_prewarm:
            await warm_pool(engine, settings.db_pool_size)
        redis = _create_redis(settings) if settings.booking_strategy == "redis_lock" else None
        strategy = build_strategy(
            settings, redis=redis, on_redis_fallback=metrics.redis_fallbacks.inc
        )

        app.state.settings = settings
        app.state.engine = engine
        app.state.redis = redis
        app.state.metrics = metrics
        app.state.catalog = CatalogService(engine)
        app.state.bookings = BookingService(engine, strategy, settings, metrics)

        async def purge() -> None:
            purged = await purge_expired_idempotency_keys(
                engine, batch_size=settings.idempotency_purge_batch_size
            )
            metrics.idempotency_keys_purged.inc(purged)

        purge_task = (
            asyncio.create_task(run_periodically(settings.idempotency_purge_interval_s, purge))
            if settings.idempotency_purge_interval_s > 0
            else None
        )
        logger.info(
            "started",
            extra={"fields": {"version": __version__, "strategy": strategy.name}},
        )
        try:
            yield
        finally:
            if purge_task is not None:
                purge_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await purge_task
            if redis is not None:
                await redis.aclose()
            await engine.dispose()
            logger.info("stopped")

    app = FastAPI(
        title="DistriBook",
        version=__version__,
        summary="Concurrency-correct seat booking.",
        lifespan=lifespan,
    )
    register_problem_handlers(app)
    app.add_middleware(RequestContextMiddleware, metrics=metrics)
    app.include_router(health.router)
    app.include_router(events.router)
    app.include_router(bookings.router)

    @app.get("/metrics", include_in_schema=False)
    async def prometheus_metrics() -> Response:
        body, content_type = metrics.render()
        return Response(body, media_type=content_type)

    return app
