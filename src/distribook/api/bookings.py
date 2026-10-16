from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

from distribook.api.deps import BookingsDep
from distribook.api.problems import PROBLEM_JSON
from distribook.domain.models import BookingCancel, BookingCreate, BookingOut
from distribook.services.idempotency import validate_idempotency_key

router = APIRouter(prefix="/v1", tags=["bookings"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"content": {PROBLEM_JSON: {}}} for code in (400, 404, 409, 422, 503)
}


@router.post(
    "/bookings",
    status_code=201,
    response_model=BookingOut,
    responses=_ERRORS,
    summary="Book 1-10 seats of one event, all or nothing",
)
async def create_booking(
    body: BookingCreate,
    bookings: BookingsDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    outcome = await bookings.create(body, validate_idempotency_key(idempotency_key))
    headers: dict[str, str] = {}
    if outcome.replayed:
        headers["Idempotent-Replayed"] = "true"
    if outcome.status_code == 201:
        headers["Location"] = f"/v1/bookings/{outcome.body['id']}"
    return JSONResponse(
        outcome.body,
        outcome.status_code,
        headers=headers,
        media_type="application/json" if outcome.status_code < 400 else PROBLEM_JSON,
    )


@router.get("/bookings/{booking_id}")
async def get_booking(booking_id: UUID, bookings: BookingsDep) -> BookingOut:
    return await bookings.get(booking_id)


@router.post("/bookings/{booking_id}/cancel")
async def cancel_booking(
    booking_id: UUID, body: BookingCancel, bookings: BookingsDep
) -> BookingOut:
    return await bookings.cancel(booking_id, body.user_id)
