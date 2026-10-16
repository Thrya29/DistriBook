from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from distribook.strategies.base import BookingStrategy, ClaimResult

_LOCK = """
    SELECT id, CAST(status AS text) AS status FROM seats
    WHERE event_id = :event_id AND id = ANY(:seat_ids)
    ORDER BY id
    FOR UPDATE{nowait}
"""
_WRITE = text(
    """
    UPDATE seats
    SET status = 'BOOKED', booking_id = :booking_id, version = version + 1, updated_at = now()
    WHERE id = ANY(:seat_ids)
    """
)


class PessimisticStrategy(BookingStrategy):
    """Textbook lock, check, write.

    Every requested row is locked (available or not) in ascending id order, the statuses are
    checked in the application, then the rows are written. Compared with ``conditional`` this
    keeps the lock across an extra round trip, and a waiter still acquires the lock after the
    winner commits, so waiters pass the lock along one at a time.

    With ``nowait=True`` a locked row fails at once (55P03, mapped to 409 SEAT_CONTENDED)
    instead of queueing: losers give up immediately and free their connection.
    """

    def __init__(self, *, nowait: bool = False) -> None:
        self.name = "pessimistic_nowait" if nowait else "pessimistic"
        self._lock = text(_LOCK.format(nowait=" NOWAIT" if nowait else ""))

    async def claim(
        self, conn: AsyncConnection, *, event_id: int, seat_ids: Sequence[int], booking_id: UUID
    ) -> ClaimResult:
        params = {"event_id": event_id, "seat_ids": sorted(seat_ids)}
        rows = (await conn.execute(self._lock, params)).all()
        taken = tuple(row.id for row in rows if row.status != "AVAILABLE")
        if taken:
            return ClaimResult(unavailable=taken)
        await conn.execute(_WRITE, {**params, "booking_id": booking_id})
        return ClaimResult(unavailable=())
