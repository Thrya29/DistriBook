"""Booking orchestration: one transaction per attempt (see docs/system-design.md section 5)."""

import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from distribook.config import Settings
from distribook.db.errors import LOCK_NOT_AVAILABLE, constraint_name, sqlstate
from distribook.domain.errors import (
    BookingNotFound,
    EventNotFound,
    IdempotencyKeyReused,
    InvalidSeats,
    RequestInProgress,
    SeatContended,
    SeatUnavailable,
)
from distribook.domain.models import BookedSeat, BookingCreate, BookingOut
from distribook.services.idempotency import request_fingerprint
from distribook.services.retry import run_with_retries
from distribook.strategies import BookingStrategy

ONE_ACTIVE_CLAIM_INDEX = "booking_seats_one_active_claim"


@dataclass(frozen=True, slots=True)
class BookingOutcome:
    status_code: int
    body: dict[str, Any]
    replayed: bool = False


class BookingService:
    def __init__(self, engine: AsyncEngine, strategy: BookingStrategy, settings: Settings) -> None:
        self._engine = engine
        self._strategy = strategy
        self._settings = settings

    @property
    def strategy_name(self) -> str:
        return self._strategy.name

    # ------------------------------------------------------------------ create

    async def create(self, request: BookingCreate, idempotency_key: str) -> BookingOutcome:
        fingerprint = request_fingerprint(request)
        return await run_with_retries(
            lambda: self._create_once(request, idempotency_key, fingerprint),
            attempts=self._settings.txn_max_attempts,
        )

    async def _create_once(
        self, request: BookingCreate, key: str, fingerprint: bytes
    ) -> BookingOutcome:
        async with self._engine.connect() as conn, conn.begin():
            labels = await self._load_seat_labels(conn, request)

            if not await self._reserve_key(conn, request.user_id, key, fingerprint):
                return await self._replay(conn, request.user_id, key, fingerprint)

            booking_id = uuid4()
            savepoint = await conn.begin_nested()
            try:
                booking = await self._claim(conn, request, booking_id, labels)
            except SeatUnavailable as exc:
                # Undo the half-built booking but keep the idempotency row, so the final
                # 409 is recorded against the key and replays are deterministic.
                await savepoint.rollback()
                outcome = BookingOutcome(409, exc.problem())
                await self._record_outcome(conn, request.user_id, key, outcome, booking_id=None)
            else:
                await savepoint.commit()
                outcome = BookingOutcome(201, booking.model_dump(mode="json"))
                await self._record_outcome(conn, request.user_id, key, outcome, booking_id)
        # Reached only after COMMIT succeeded: a 201 always refers to a durable booking.
        return outcome

    async def _load_seat_labels(
        self, conn: AsyncConnection, request: BookingCreate
    ) -> dict[int, str]:
        exists = await conn.scalar(
            text("SELECT EXISTS (SELECT 1 FROM events WHERE id = :event_id)"),
            {"event_id": request.event_id},
        )
        if not exists:
            raise EventNotFound()
        rows = await conn.execute(
            text("SELECT id, label FROM seats WHERE event_id = :event_id AND id = ANY(:seat_ids)"),
            {"event_id": request.event_id, "seat_ids": list(request.seat_ids)},
        )
        labels: dict[int, str] = {row.id: row.label for row in rows}
        missing = sorted(set(request.seat_ids) - labels.keys())
        if missing:
            raise InvalidSeats(invalid_seat_ids=missing)
        return labels

    async def _reserve_key(
        self, conn: AsyncConnection, user_id: str, key: str, fingerprint: bytes
    ) -> bool:
        """Claim the idempotency key for this transaction; False if a live row already exists.

        If another transaction holds the same key uncommitted, the INSERT waits for it to finish
        (bounded by lock_timeout), so concurrent duplicates wait for the original only.

        ON CONFLICT DO NOTHING is deliberate. DO UPDATE ... WHERE would lock the existing row
        even when its WHERE is false, so every duplicate would hold that lock until its own
        transaction ended and duplicates would queue behind each other (a lock convoy). An
        expired row is instead recycled by a separate conditional UPDATE, on the rare path only.
        """
        params = {
            "user_id": user_id,
            "key": key,
            "request_hash": fingerprint,
            "ttl_s": float(self._settings.idempotency_ttl_s),
        }
        try:
            inserted = await conn.scalar(
                text(
                    """
                    INSERT INTO idempotency_keys (user_id, key, request_hash, expires_at)
                    VALUES (:user_id, :key, :request_hash,
                            now() + make_interval(secs => :ttl_s))
                    ON CONFLICT (user_id, key) DO NOTHING
                    RETURNING true
                    """
                ),
                params,
            )
            if inserted:
                return True
            # A plain UPDATE does not lock a row whose WHERE is false, so for a live key this
            # is lock-free. For an expired key, exactly one concurrent caller wins the recycle;
            # the others wait for it and then see a live key.
            recycled = await conn.scalar(
                text(
                    """
                    UPDATE idempotency_keys
                    SET request_hash = :request_hash,
                        response_code = NULL,
                        response_body = NULL,
                        booking_id = NULL,
                        created_at = now(),
                        expires_at = now() + make_interval(secs => :ttl_s)
                    WHERE user_id = :user_id AND key = :key AND expires_at <= now()
                    RETURNING true
                    """
                ),
                params,
            )
        except DBAPIError as exc:
            if sqlstate(exc) == LOCK_NOT_AVAILABLE:
                raise RequestInProgress() from exc
            raise
        return bool(recycled)

    async def _replay(
        self, conn: AsyncConnection, user_id: str, key: str, fingerprint: bytes
    ) -> BookingOutcome:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT request_hash, response_code, CAST(response_body AS text) AS body
                    FROM idempotency_keys WHERE user_id = :user_id AND key = :key
                    """
                ),
                {"user_id": user_id, "key": key},
            )
        ).one()
        if bytes(row.request_hash) != fingerprint:
            raise IdempotencyKeyReused()
        if row.response_code is None:
            # The owning transaction always records an outcome before it commits, so a
            # committed row without one would mean a broken invariant, not a race.
            raise RuntimeError("idempotency row committed without an outcome")
        return BookingOutcome(row.response_code, json.loads(row.body), replayed=True)

    async def _claim(
        self,
        conn: AsyncConnection,
        request: BookingCreate,
        booking_id: UUID,
        labels: dict[int, str],
    ) -> BookingOut:
        created = (
            await conn.execute(
                text(
                    """
                    INSERT INTO bookings (id, event_id, user_id)
                    VALUES (:id, :event_id, :user_id)
                    RETURNING created_at
                    """
                ),
                {"id": booking_id, "event_id": request.event_id, "user_id": request.user_id},
            )
        ).one()

        try:
            result = await self._strategy.claim(
                conn, event_id=request.event_id, seat_ids=request.seat_ids, booking_id=booking_id
            )
        except DBAPIError as exc:
            if sqlstate(exc) == LOCK_NOT_AVAILABLE:
                raise SeatContended() from exc
            raise
        if not result.ok:
            raise SeatUnavailable(unavailable_seat_ids=list(result.unavailable))

        try:
            await conn.execute(
                text(
                    """
                    INSERT INTO booking_seats (booking_id, event_id, seat_id)
                    SELECT :booking_id, :event_id, unnest(CAST(:seat_ids AS bigint[]))
                    """
                ),
                {
                    "booking_id": booking_id,
                    "event_id": request.event_id,
                    "seat_ids": sorted(request.seat_ids),
                },
            )
        except IntegrityError as exc:
            # The database safety net caught a double claim the strategy let through.
            if constraint_name(exc) == ONE_ACTIVE_CLAIM_INDEX:
                raise SeatUnavailable(unavailable_seat_ids=sorted(request.seat_ids)) from exc
            raise

        return BookingOut(
            id=booking_id,
            event_id=request.event_id,
            user_id=request.user_id,
            status="CONFIRMED",
            seats=[BookedSeat(id=sid, label=labels[sid]) for sid in sorted(request.seat_ids)],
            created_at=created.created_at,
            cancelled_at=None,
        )

    async def _record_outcome(
        self,
        conn: AsyncConnection,
        user_id: str,
        key: str,
        outcome: BookingOutcome,
        booking_id: UUID | None,
    ) -> None:
        await conn.execute(
            text(
                """
                UPDATE idempotency_keys
                SET response_code = :code,
                    response_body = CAST(CAST(:body AS text) AS jsonb),
                    booking_id = :booking_id
                WHERE user_id = :user_id AND key = :key
                """
            ),
            {
                "code": outcome.status_code,
                "body": json.dumps(outcome.body),
                "booking_id": booking_id,
                "user_id": user_id,
                "key": key,
            },
        )

    # ------------------------------------------------------------------ read

    async def get(self, booking_id: UUID) -> BookingOut:
        async with self._engine.connect() as conn:
            return await self._load(conn, booking_id)

    async def _load(self, conn: AsyncConnection, booking_id: UUID) -> BookingOut:
        booking = (
            await conn.execute(
                text(
                    """
                    SELECT id, event_id, user_id, CAST(status AS text) AS status,
                           created_at, cancelled_at
                    FROM bookings WHERE id = :id
                    """
                ),
                {"id": booking_id},
            )
        ).one_or_none()
        if booking is None:
            raise BookingNotFound()
        seats = await conn.execute(
            text(
                """
                SELECT s.id, s.label FROM booking_seats bs JOIN seats s ON s.id = bs.seat_id
                WHERE bs.booking_id = :id ORDER BY s.id
                """
            ),
            {"id": booking_id},
        )
        return BookingOut(
            id=booking.id,
            event_id=booking.event_id,
            user_id=booking.user_id,
            status=booking.status,
            seats=[BookedSeat(id=s.id, label=s.label) for s in seats],
            created_at=booking.created_at,
            cancelled_at=booking.cancelled_at,
        )

    # ------------------------------------------------------------------ cancel

    async def cancel(self, booking_id: UUID, user_id: str) -> BookingOut:
        return await run_with_retries(
            lambda: self._cancel_once(booking_id, user_id),
            attempts=self._settings.txn_max_attempts,
        )

    async def _cancel_once(self, booking_id: UUID, user_id: str) -> BookingOut:
        async with self._engine.connect() as conn, conn.begin():
            owner = (
                await conn.execute(
                    text(
                        """
                        SELECT user_id, CAST(status AS text) AS status
                        FROM bookings WHERE id = :id FOR UPDATE
                        """
                    ),
                    {"id": booking_id},
                )
            ).one_or_none()
            # A wrong owner gets the same 404 as a missing booking: ids are not enumerable.
            if owner is None or owner.user_id != user_id:
                raise BookingNotFound()

            if owner.status == "CONFIRMED":
                params = {"id": booking_id}
                # Lock order rule: the booking row first, then its seats in ascending id order.
                await conn.execute(
                    text("SELECT id FROM seats WHERE booking_id = :id ORDER BY id FOR UPDATE"),
                    params,
                )
                await conn.execute(
                    text(
                        """
                        UPDATE seats
                        SET status = 'AVAILABLE', booking_id = NULL,
                            version = version + 1, updated_at = now()
                        WHERE booking_id = :id
                        """
                    ),
                    params,
                )
                await conn.execute(
                    text(
                        """
                        UPDATE booking_seats SET released_at = now()
                        WHERE booking_id = :id AND released_at IS NULL
                        """
                    ),
                    params,
                )
                await conn.execute(
                    text(
                        """
                        UPDATE bookings SET status = 'CANCELLED', cancelled_at = now()
                        WHERE id = :id
                        """
                    ),
                    params,
                )
            return await self._load(conn, booking_id)
