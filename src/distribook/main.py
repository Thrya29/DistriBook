"""Application factory. Run with: ``uvicorn distribook.main:create_app --factory``."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from distribook import __version__
from distribook.api import bookings, events, health
from distribook.api.problems import register_problem_handlers
from distribook.config import Settings
from distribook.db.engine import create_engine
from distribook.services.booking_service import BookingService
from distribook.services.catalog_service import CatalogService
from distribook.strategies import build_strategy


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings)
        app.state.settings = settings
        app.state.engine = engine
        app.state.catalog = CatalogService(engine)
        app.state.bookings = BookingService(engine, build_strategy(settings), settings)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="DistriBook",
        version=__version__,
        summary="Concurrency-correct seat booking.",
        lifespan=lifespan,
    )
    register_problem_handlers(app)
    app.include_router(health.router)
    app.include_router(events.router)
    app.include_router(bookings.router)
    return app
