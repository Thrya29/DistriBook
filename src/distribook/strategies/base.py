from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True, slots=True)
class ClaimResult:
    """Outcome of trying to claim a set of seats. ``unavailable`` is empty on success."""

    unavailable: tuple[int, ...]

    @property
    def ok(self) -> bool:
        return not self.unavailable


class BookingStrategy(Protocol):
    """Claims seats for a booking inside the caller's open transaction.

    Contract:
    - On success every requested seat is ``BOOKED`` with ``booking_id`` set, in this transaction.
    - On failure it returns the seats that could not be claimed. Any partial changes it made are
      undone by the caller rolling back to its savepoint.
    - Row locks on multiple seats are acquired in ascending seat id order.
    - Lock waits are bounded by the session ``lock_timeout``; the resulting error propagates.
    """

    name: str

    async def claim(
        self, conn: AsyncConnection, *, event_id: int, seat_ids: Sequence[int], booking_id: UUID
    ) -> ClaimResult: ...
