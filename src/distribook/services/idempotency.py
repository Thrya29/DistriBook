import hashlib
import json
import re

from distribook.domain.errors import IdempotencyKeyInvalid, IdempotencyKeyMissing
from distribook.domain.models import BookingCreate

# Visible ASCII only: keys are echoed in logs and must not carry whitespace or control bytes.
_KEY_PATTERN = re.compile(r"[\x21-\x7e]{1,255}")


def validate_idempotency_key(key: str | None) -> str:
    if key is None:
        raise IdempotencyKeyMissing()
    if not _KEY_PATTERN.fullmatch(key):
        raise IdempotencyKeyInvalid("Expected 1-255 visible ASCII characters.")
    return key


def request_fingerprint(request: BookingCreate) -> bytes:
    """SHA-256 over a canonical form of the request.

    Seat order is irrelevant to the meaning of a booking, so ids are sorted: a retry that lists
    the same seats in a different order is the same request.
    """
    canonical = json.dumps(
        {
            "event_id": request.event_id,
            "user_id": request.user_id,
            "seat_ids": sorted(request.seat_ids),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).digest()
