from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from distribook.strategies.base import ClaimResult

# The availability check and the write are a single statement. The CTE locks only rows that are
# still AVAILABLE, in ascending id order (deadlock-free). A transaction that had to wait for a row
# lock re-evaluates `status = 'AVAILABLE'` against the committed row version once the lock is
# released (READ COMMITTED update semantics), so a loser skips the seat instead of overwriting.
_CLAIM = text(
    """
    WITH target AS (
        SELECT id FROM seats
        WHERE event_id = :event_id AND id = ANY(:seat_ids) AND status = 'AVAILABLE'
        ORDER BY id
        FOR UPDATE
    )
    UPDATE seats s
    SET status = 'BOOKED', booking_id = :booking_id, version = s.version + 1, updated_at = now()
    FROM target
    WHERE s.id = target.id
    RETURNING s.id
    """
)


class ConditionalStrategy:
    name = "conditional"

    async def claim(
        self, conn: AsyncConnection, *, event_id: int, seat_ids: Sequence[int], booking_id: UUID
    ) -> ClaimResult:
        result = await conn.execute(
            _CLAIM,
            {"event_id": event_id, "seat_ids": sorted(seat_ids), "booking_id": booking_id},
        )
        claimed = {row[0] for row in result}
        return ClaimResult(unavailable=tuple(sorted(set(seat_ids) - claimed)))
