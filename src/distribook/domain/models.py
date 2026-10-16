"""Request and response models of the public API."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

BIGINT_MAX = 2**63 - 1
MAX_SEATS_PER_BOOKING = 10

Id = Annotated[int, Field(gt=0, le=BIGINT_MAX)]
UserId = Annotated[str, Field(min_length=1, max_length=128)]
SeatStatus = Literal["AVAILABLE", "BOOKED"]
BookingStatus = Literal["CONFIRMED", "CANCELLED"]


class _Request(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EventCreate(_Request):
    name: str = Field(min_length=1, max_length=200)
    starts_at: AwareDatetime
    rows: int = Field(ge=1, le=100)
    seats_per_row: int = Field(ge=1, le=100)


class SeatCounts(BaseModel):
    total: int
    available: int
    booked: int


class EventOut(BaseModel):
    id: int
    name: str
    starts_at: datetime
    created_at: datetime
    seat_counts: SeatCounts


class SeatOut(BaseModel):
    id: int
    event_id: int
    label: str
    status: SeatStatus


class SeatPage(BaseModel):
    items: list[SeatOut]
    next_after_id: int | None = Field(
        description="Pass as `after_id` to fetch the next page; null on the last page."
    )


class BookingCreate(_Request):
    event_id: Id
    user_id: UserId
    seat_ids: list[Id] = Field(min_length=1, max_length=MAX_SEATS_PER_BOOKING)

    @field_validator("seat_ids")
    @classmethod
    def _no_duplicates(cls, seat_ids: list[int]) -> list[int]:
        if len(set(seat_ids)) != len(seat_ids):
            raise ValueError("seat_ids must not contain duplicates")
        return seat_ids


class BookingCancel(_Request):
    user_id: UserId


class BookedSeat(BaseModel):
    id: int
    label: str


class BookingOut(BaseModel):
    id: UUID
    event_id: int
    user_id: str
    status: BookingStatus
    seats: list[BookedSeat]
    created_at: datetime
    cancelled_at: datetime | None
