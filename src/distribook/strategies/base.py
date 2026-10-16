from abc import ABC, abstractmethod
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager, nullcontext
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True, slots=True)
class ClaimResult:
    """Outcome of trying to claim a set of seats. ``unavailable`` is empty on success."""

    unavailable: tuple[int, ...]

    @property
    def ok(self) -> bool:
        return not self.unavailable


class BookingStrategy(ABC):
    """Concurrency control for claiming seats.

    ``claim`` contract:
    - runs inside the caller's open transaction;
    - on success every requested seat is ``BOOKED`` with ``booking_id`` set;
    - on failure returns the seats that could not be claimed; the caller rolls back to its
      savepoint, undoing any partial changes;
    - row locks on several seats are taken in ascending seat id order;
    - lock waits are bounded by the session ``lock_timeout``; that error propagates.
    """

    name: str

    def guard(self, seat_ids: Sequence[int]) -> AbstractAsyncContextManager[None]:
        """Entered before a database connection is taken. A strategy may reject the request
        here, cheaply, by raising ``SeatContended``. The default does nothing."""
        return nullcontext()

    @abstractmethod
    async def claim(
        self, conn: AsyncConnection, *, event_id: int, seat_ids: Sequence[int], booking_id: UUID
    ) -> ClaimResult: ...
