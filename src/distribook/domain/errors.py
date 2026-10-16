"""Domain errors. Each maps to one RFC 9457 problem response with a stable ``code``."""

from typing import Any


class DomainError(Exception):
    status: int = 500
    code: str = "INTERNAL_ERROR"
    title: str = "Internal error"
    retry_after_s: int | None = None

    def __init__(self, detail: str | None = None, **extensions: Any) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.extensions = extensions

    def problem(self) -> dict[str, Any]:
        """RFC 9457 problem details body."""
        body: dict[str, Any] = {
            "type": "/problems/" + self.code.lower().replace("_", "-"),
            "title": self.title,
            "status": self.status,
            "code": self.code,
        }
        if self.detail:
            body["detail"] = self.detail
        body.update(self.extensions)
        return body


class IdempotencyKeyMissing(DomainError):
    status, code, title = 400, "IDEMPOTENCY_KEY_MISSING", "Idempotency-Key header is required"


class IdempotencyKeyInvalid(DomainError):
    status, code, title = 400, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key header is malformed"


class EventNotFound(DomainError):
    status, code, title = 404, "EVENT_NOT_FOUND", "Event not found"


class SeatNotFound(DomainError):
    status, code, title = 404, "SEAT_NOT_FOUND", "Seat not found"


class BookingNotFound(DomainError):
    status, code, title = 404, "BOOKING_NOT_FOUND", "Booking not found"


class SeatUnavailable(DomainError):
    status, code, title = 409, "SEAT_UNAVAILABLE", "One or more seats are already booked"


class SeatContended(DomainError):
    status, code, title = 409, "SEAT_CONTENDED", "Seat is being booked by another request"
    retry_after_s = 1


class RequestInProgress(DomainError):
    status, code, title = 409, "REQUEST_IN_PROGRESS", "A request with this key is still running"
    retry_after_s = 1


class InvalidSeats(DomainError):
    status, code, title = 422, "INVALID_SEATS", "Seats do not belong to this event"


class IdempotencyKeyReused(DomainError):
    status, code, title = (
        422,
        "IDEMPOTENCY_KEY_REUSED",
        "Idempotency-Key was already used with a different request",
    )


class ServiceOverloaded(DomainError):
    status, code, title = 503, "SERVICE_OVERLOADED", "Service is overloaded; retry shortly"
    retry_after_s = 1


class DatabaseUnavailable(DomainError):
    status, code, title = 503, "DATABASE_UNAVAILABLE", "Database is unavailable; retry shortly"
    retry_after_s = 1
