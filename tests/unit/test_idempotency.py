import pytest

from distribook.domain.errors import IdempotencyKeyInvalid, IdempotencyKeyMissing
from distribook.domain.models import BookingCreate
from distribook.services.idempotency import request_fingerprint, validate_idempotency_key


def booking(**overrides: object) -> BookingCreate:
    fields: dict[str, object] = {"event_id": 1, "user_id": "u-1", "seat_ids": [3, 1, 2]}
    fields.update(overrides)
    return BookingCreate.model_validate(fields)


def test_fingerprint_ignores_seat_order() -> None:
    assert request_fingerprint(booking(seat_ids=[1, 2, 3])) == request_fingerprint(booking())


@pytest.mark.parametrize(
    "change", [{"event_id": 2}, {"user_id": "u-2"}, {"seat_ids": [1, 2]}, {"seat_ids": [1, 2, 4]}]
)
def test_fingerprint_changes_with_meaning(change: dict[str, object]) -> None:
    assert request_fingerprint(booking(**change)) != request_fingerprint(booking())


def test_fingerprint_is_sha256() -> None:
    assert len(request_fingerprint(booking())) == 32


def test_missing_key_is_rejected() -> None:
    with pytest.raises(IdempotencyKeyMissing):
        validate_idempotency_key(None)


@pytest.mark.parametrize("key", ["", " ", "has space", "tab\t", "é", "x" * 256])
def test_malformed_key_is_rejected(key: str) -> None:
    with pytest.raises(IdempotencyKeyInvalid):
        validate_idempotency_key(key)


@pytest.mark.parametrize("key", ["a", "3f1c2a9e-8d7b-4c55-a8a4-0d6f1e2b7c90", "x" * 255])
def test_wellformed_key_is_accepted(key: str) -> None:
    assert validate_idempotency_key(key) == key
