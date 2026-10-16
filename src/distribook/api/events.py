from typing import Annotated

from fastapi import APIRouter, Path, Query, Response

from distribook.api.deps import CatalogDep
from distribook.domain.models import (
    BIGINT_MAX,
    EventCreate,
    EventOut,
    SeatOut,
    SeatPage,
    SeatStatus,
)

router = APIRouter(prefix="/v1", tags=["events & seats"])

PathId = Annotated[int, Path(gt=0, le=BIGINT_MAX)]


@router.post("/events", status_code=201)
async def create_event(body: EventCreate, catalog: CatalogDep, response: Response) -> EventOut:
    event = await catalog.create_event(body)
    response.headers["Location"] = f"/v1/events/{event.id}"
    return event


@router.get("/events/{event_id}")
async def get_event(event_id: PathId, catalog: CatalogDep) -> EventOut:
    return await catalog.get_event(event_id)


@router.get("/events/{event_id}/seats")
async def list_seats(
    event_id: PathId,
    catalog: CatalogDep,
    status: SeatStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    after_id: Annotated[int, Query(ge=0, le=BIGINT_MAX)] = 0,
) -> SeatPage:
    return await catalog.list_seats(event_id, status=status, limit=limit, after_id=after_id)


@router.get("/seats/{seat_id}")
async def get_seat(seat_id: PathId, catalog: CatalogDep) -> SeatOut:
    return await catalog.get_seat(seat_id)
