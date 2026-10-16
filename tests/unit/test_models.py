from datetime import datetime

import pytest
from pydantic import ValidationError

from distribook.config import Settings
from distribook.domain.models import BIGINT_MAX, BookingCreate, EventCreate


def test_booking_rejects_duplicate_seats() -> None:
    with pytest.raises(ValidationError, match="duplicates"):
        BookingCreate(event_id=1, user_id="u", seat_ids=[1, 1])


@pytest.mark.parametrize("seat_ids", [[], list(range(1, 12))])
def test_booking_seat_count_bounds(seat_ids: list[int]) -> None:
    with pytest.raises(ValidationError):
        BookingCreate(event_id=1, user_id="u", seat_ids=seat_ids)


@pytest.mark.parametrize("bad_id", [0, -1, BIGINT_MAX + 1])
def test_booking_ids_must_fit_bigint(bad_id: int) -> None:
    with pytest.raises(ValidationError):
        BookingCreate(event_id=bad_id, user_id="u", seat_ids=[1])


def test_booking_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        BookingCreate.model_validate({"event_id": 1, "user_id": "u", "seat_ids": [1], "price": 0})


def test_event_requires_timezone() -> None:
    with pytest.raises(ValidationError):
        EventCreate(name="x", starts_at=datetime(2030, 1, 1), rows=1, seats_per_row=1)


def test_lock_timeout_must_be_below_statement_timeout() -> None:
    with pytest.raises(ValidationError):
        Settings(db_lock_timeout_ms=2000, db_statement_timeout_ms=2000)


def test_unsafe_strategy_requires_explicit_opt_in() -> None:
    with pytest.raises(ValidationError, match="can double-book"):
        Settings(booking_strategy="naive")


def test_unsafe_strategy_is_never_allowed_in_production() -> None:
    with pytest.raises(ValidationError, match="never allowed in production"):
        Settings(booking_strategy="naive", enable_unsafe_strategies=True, env="production")
