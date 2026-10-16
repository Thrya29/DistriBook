"""Correctness invariant checks (docs/requirements.md section 6).

Each query returns the offending rows; an empty result means the invariant holds. Run after
every concurrency test and every benchmark run.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

INVARIANT_QUERIES: dict[str, str] = {
    "INV-1 a seat has at most one active claim": """
        SELECT seat_id FROM booking_seats WHERE released_at IS NULL
        GROUP BY seat_id HAVING count(*) > 1
    """,
    "INV-3 seats agree with the claim history": """
        SELECT s.id FROM seats s
        LEFT JOIN booking_seats bs ON bs.seat_id = s.id AND bs.released_at IS NULL
        WHERE (s.status = 'BOOKED') IS DISTINCT FROM (bs.booking_id IS NOT NULL)
           OR (s.status = 'BOOKED' AND s.booking_id IS DISTINCT FROM bs.booking_id)
    """,
    "INV-2 bookings are applied completely": """
        SELECT b.id FROM bookings b
        LEFT JOIN booking_seats bs ON bs.booking_id = b.id
        GROUP BY b.id, b.status
        HAVING count(bs.seat_id) = 0
            OR (b.status = 'CONFIRMED' AND bool_or(bs.released_at IS NOT NULL))
            OR (b.status = 'CANCELLED' AND bool_or(bs.released_at IS NULL))
    """,
    "INV-4 one booking per idempotency key": """
        SELECT user_id, key FROM idempotency_keys WHERE booking_id IS NOT NULL
        GROUP BY user_id, key HAVING count(DISTINCT booking_id) > 1
    """,
    "INV-5 booked seats belong to the booking's event": """
        SELECT bs.booking_id, bs.seat_id FROM booking_seats bs
        JOIN bookings b ON b.id = bs.booking_id
        JOIN seats s ON s.id = bs.seat_id
        WHERE b.event_id <> s.event_id
    """,
}


async def find_violations(conn: AsyncConnection) -> dict[str, list[tuple[object, ...]]]:
    violations: dict[str, list[tuple[object, ...]]] = {}
    for name, query in INVARIANT_QUERIES.items():
        rows = [tuple(row) for row in await conn.execute(text(query))]
        if rows:
            violations[name] = rows
    return violations
