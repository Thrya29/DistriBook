"""Events and seats: creation and read paths."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from distribook.domain.errors import EventNotFound, SeatNotFound
from distribook.domain.models import (
    EventCreate,
    EventOut,
    SeatCounts,
    SeatOut,
    SeatPage,
    SeatStatus,
)


def row_label(index: int) -> str:
    """Spreadsheet-style row names: 0 -> A, 25 -> Z, 26 -> AA, 27 -> AB."""
    if index < 0:
        raise ValueError("row index must be non-negative")
    label = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label


def seat_labels(rows: int, seats_per_row: int) -> list[str]:
    return [f"{row_label(r)}{n}" for r in range(rows) for n in range(1, seats_per_row + 1)]


class CatalogService:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create_event(self, request: EventCreate) -> EventOut:
        labels = seat_labels(request.rows, request.seats_per_row)
        async with self._engine.connect() as conn, conn.begin():
            event = (
                await conn.execute(
                    text(
                        """
                        INSERT INTO events (name, starts_at) VALUES (:name, :starts_at)
                        RETURNING id, name, starts_at, created_at
                        """
                    ),
                    {"name": request.name, "starts_at": request.starts_at},
                )
            ).one()
            # unnest preserves array order, so seat ids increase row by row.
            await conn.execute(
                text(
                    """
                    INSERT INTO seats (event_id, label)
                    SELECT :event_id, unnest(CAST(:labels AS text[]))
                    """
                ),
                {"event_id": event.id, "labels": labels},
            )
        return EventOut(
            id=event.id,
            name=event.name,
            starts_at=event.starts_at,
            created_at=event.created_at,
            seat_counts=SeatCounts(total=len(labels), available=len(labels), booked=0),
        )

    async def get_event(self, event_id: int) -> EventOut:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT e.id, e.name, e.starts_at, e.created_at,
                               count(s.id) AS total,
                               count(s.id) FILTER (WHERE s.status = 'AVAILABLE') AS available,
                               count(s.id) FILTER (WHERE s.status = 'BOOKED') AS booked
                        FROM events e LEFT JOIN seats s ON s.event_id = e.id
                        WHERE e.id = :event_id
                        GROUP BY e.id
                        """
                    ),
                    {"event_id": event_id},
                )
            ).one_or_none()
        if row is None:
            raise EventNotFound()
        return EventOut(
            id=row.id,
            name=row.name,
            starts_at=row.starts_at,
            created_at=row.created_at,
            seat_counts=SeatCounts(total=row.total, available=row.available, booked=row.booked),
        )

    async def list_seats(
        self, event_id: int, *, status: SeatStatus | None, limit: int, after_id: int
    ) -> SeatPage:
        status_filter = "AND status = CAST(:status AS seat_status)" if status else ""
        query = text(
            f"""
            SELECT id, event_id, label, CAST(status AS text) AS status
            FROM seats
            WHERE event_id = :event_id AND id > :after_id {status_filter}
            ORDER BY id
            LIMIT :limit
            """  # noqa: S608 - only a fixed fragment is interpolated; values are bound
        )
        params: dict[str, object] = {"event_id": event_id, "after_id": after_id, "limit": limit + 1}
        if status:
            params["status"] = status
        async with self._engine.connect() as conn:
            await self._require_event(conn, event_id)
            rows = (await conn.execute(query, params)).all()
        has_more = len(rows) > limit
        items = [
            SeatOut(id=r.id, event_id=r.event_id, label=r.label, status=r.status)
            for r in rows[:limit]
        ]
        return SeatPage(items=items, next_after_id=items[-1].id if has_more else None)

    async def get_seat(self, seat_id: int) -> SeatOut:
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        """
                        SELECT id, event_id, label, CAST(status AS text) AS status
                        FROM seats WHERE id = :id
                        """
                    ),
                    {"id": seat_id},
                )
            ).one_or_none()
        if row is None:
            raise SeatNotFound()
        return SeatOut(id=row.id, event_id=row.event_id, label=row.label, status=row.status)

    @staticmethod
    async def _require_event(conn: AsyncConnection, event_id: int) -> None:
        exists = await conn.scalar(
            text("SELECT EXISTS (SELECT 1 FROM events WHERE id = :id)"), {"id": event_id}
        )
        if not exists:
            raise EventNotFound()
