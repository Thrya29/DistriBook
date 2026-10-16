"""Deliberately UNSAFE strategy: check, then write, with nothing tying the two together.

It exists only to prove that the concurrency tests can detect a race (docs/system-design.md
section 6.4). Configuration refuses it unless unsafe strategies are explicitly enabled, and
always refuses it in production.
"""

import asyncio
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from distribook.strategies.base import BookingStrategy, ClaimResult

_READ = text("SELECT id, status FROM seats WHERE event_id = :event_id AND id = ANY(:seat_ids)")
_WRITE = text(
    """
    UPDATE seats
    SET status = 'BOOKED', booking_id = :booking_id, version = version + 1, updated_at = now()
    WHERE id = ANY(:seat_ids)
    """
)


class NaiveStrategy(BookingStrategy):
    name = "naive"

    def __init__(self, race_window_s: float = 0.0) -> None:
        # Tests widen the gap between check and write so the race reproduces every run
        # instead of depending on scheduler luck.
        self._race_window_s = race_window_s

    async def claim(
        self, conn: AsyncConnection, *, event_id: int, seat_ids: Sequence[int], booking_id: UUID
    ) -> ClaimResult:
        params = {"event_id": event_id, "seat_ids": sorted(seat_ids)}
        rows = (await conn.execute(_READ, params)).all()
        taken = tuple(sorted(row.id for row in rows if row.status != "AVAILABLE"))
        if taken:
            return ClaimResult(unavailable=taken)
        if self._race_window_s:
            await asyncio.sleep(self._race_window_s)
        await conn.execute(_WRITE, {**params, "booking_id": booking_id})  # unconditional
        return ClaimResult(unavailable=())
