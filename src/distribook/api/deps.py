from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine

from distribook.services.booking_service import BookingService
from distribook.services.catalog_service import CatalogService


def _engine(request: Request) -> AsyncEngine:
    return cast(AsyncEngine, request.app.state.engine)


def _catalog(request: Request) -> CatalogService:
    return cast(CatalogService, request.app.state.catalog)


def _bookings(request: Request) -> BookingService:
    return cast(BookingService, request.app.state.bookings)


EngineDep = Annotated[AsyncEngine, Depends(_engine)]
CatalogDep = Annotated[CatalogService, Depends(_catalog)]
BookingsDep = Annotated[BookingService, Depends(_bookings)]
