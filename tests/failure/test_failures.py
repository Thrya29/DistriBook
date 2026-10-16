"""Failure injection (docs/system-design.md section 8): every failure either rolls back
cleanly or fails fast with a retryable status, and a retry with the same key then succeeds."""

import hashlib
import time
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from distribook.config import Settings
from distribook.services.booking_service import BookingService
from distribook.strategies import BookingStrategy, ClaimResult
from distribook.strategies.conditional import ConditionalStrategy
from tests.conftest import client_for, running_app
from tests.integration.helpers import book, create_event, seat_ids, seat_status


class DiesAfterClaim(BookingStrategy):
    """Claims the seats, then has PostgreSQL terminate this very session before COMMIT,
    as if the API process or its network link died mid-transaction."""

    name = "dies-after-claim"

    async def claim(
        self, conn: AsyncConnection, *, event_id: int, seat_ids: Sequence[int], booking_id: UUID
    ) -> ClaimResult:
        result = await ConditionalStrategy().claim(
            conn, event_id=event_id, seat_ids=seat_ids, booking_id=booking_id
        )
        await conn.execute(text("SELECT pg_terminate_backend(pg_backend_pid())"))
        return result


async def count(app: FastAPI, table: str) -> int:
    async with app.state.engine.connect() as conn:
        value = await conn.scalar(text(f"SELECT count(*) FROM {table}"))  # noqa: S608 - constant
    return int(value or 0)


async def test_session_killed_mid_transaction_leaves_no_trace(
    app: FastAPI, client: AsyncClient, settings: Settings
) -> None:
    event = await create_event(client, rows=1, seats_per_row=1)
    seat = (await seat_ids(client, event["id"]))[0]
    healthy = app.state.bookings
    app.state.bookings = BookingService(
        app.state.engine, DiesAfterClaim(), settings, app.state.metrics
    )

    crashed = await book(client, event["id"], [seat], key="crash-1")

    assert crashed.status_code == 503
    assert crashed.json()["code"] == "DATABASE_UNAVAILABLE"
    assert crashed.headers["retry-after"] == "1"
    # Nothing from the dead transaction survived: seat free, no booking, key not consumed.
    assert await seat_status(client, seat) == "AVAILABLE"
    assert await count(app, "bookings") == 0
    assert await count(app, "idempotency_keys") == 0

    app.state.bookings = healthy
    retried = await book(client, event["id"], [seat], key="crash-1")

    assert retried.status_code == 201
    assert "idempotent-replayed" not in retried.headers


async def test_pool_recovers_after_every_connection_is_killed(
    app: FastAPI, client: AsyncClient
) -> None:
    # Warm several pooled connections, then kill them all, as a database restart would.
    event = await create_event(client, rows=1, seats_per_row=5)
    seats = await seat_ids(client, event["id"])
    async with app.state.engine.connect() as admin:
        killed = await admin.scalar(
            text(
                """
                SELECT count(pg_terminate_backend(pid)) FROM pg_stat_activity
                WHERE datname = current_database() AND application_name = 'distribook'
                  AND pid <> pg_backend_pid()
                """
            )
        )
    assert killed is not None

    # Stale connections are detected on checkout (pre-ping) and replaced transparently.
    for seat in seats:
        assert (await book(client, event["id"], [seat])).status_code == 201


async def test_unreachable_database_fails_fast(settings: Settings) -> None:
    unreachable = settings.model_copy(
        update={
            "database_url": "postgresql+asyncpg://distribook:distribook@127.0.0.1:1/distribook_test",
            "db_connect_timeout_s": 1.0,
        }
    )
    async with running_app(unreachable, reset=False) as app, client_for(app) as client:
        assert (await client.get("/healthz")).status_code == 200
        assert (await client.get("/readyz")).status_code == 503

        started = time.monotonic()
        response = await client.get("/v1/events/1")
        elapsed = time.monotonic() - started

    assert response.status_code == 503
    assert response.json()["code"] == "DATABASE_UNAVAILABLE"
    assert response.headers["retry-after"] == "1"
    assert elapsed < 3.0, f"took {elapsed:.2f}s; failing fast is the point"


async def test_exhausted_pool_sheds_load_quickly(settings: Settings) -> None:
    tiny = settings.model_copy(update={"db_pool_size": 1, "db_pool_timeout_s": 0.3})
    async with running_app(tiny) as app, client_for(app) as client:
        event = await create_event(client, rows=1, seats_per_row=1)
        async with app.state.engine.connect():  # hold the only connection
            started = time.monotonic()
            response = await client.get(f"/v1/events/{event['id']}")
            elapsed = time.monotonic() - started

        assert response.status_code == 503
        assert response.json()["code"] == "SERVICE_OVERLOADED"
        assert response.headers["retry-after"] == "1"
        assert elapsed < 1.5
        # Capacity returns as soon as the connection is released.
        assert (await client.get(f"/v1/events/{event['id']}")).status_code == 200


async def test_seat_locked_elsewhere_is_contended_and_retryable(settings: Settings) -> None:
    fast_locks = settings.model_copy(
        update={"db_lock_timeout_ms": 300, "db_statement_timeout_ms": 1000}
    )
    async with running_app(fast_locks) as app, client_for(app) as client:
        event = await create_event(client, rows=1, seats_per_row=1)
        seat = (await seat_ids(client, event["id"]))[0]
        async with app.state.engine.connect() as other, other.begin() as txn:
            await other.execute(
                text("SELECT id FROM seats WHERE id = :id FOR UPDATE"), {"id": seat}
            )
            contended = await book(client, event["id"], [seat], key="locked-1")
            await txn.rollback()

        assert contended.status_code == 409
        assert contended.json()["code"] == "SEAT_CONTENDED"
        assert contended.headers["retry-after"] == "1"
        # Temporary outcomes are not stored against the key, so the retry gets a fresh attempt.
        retried = await book(client, event["id"], [seat], key="locked-1")
        assert retried.status_code == 201


async def test_duplicate_of_a_stuck_request_reports_in_progress(settings: Settings) -> None:
    fast_locks = settings.model_copy(
        update={"db_lock_timeout_ms": 300, "db_statement_timeout_ms": 1000}
    )
    async with running_app(fast_locks) as app, client_for(app) as client:
        event = await create_event(client, rows=1, seats_per_row=1)
        seat = (await seat_ids(client, event["id"]))[0]
        body: dict[str, Any] = {"event_id": event["id"], "user_id": "alice", "seat_ids": [seat]}
        async with app.state.engine.connect() as other, other.begin() as txn:
            # Another transaction has claimed the key but not finished.
            await other.execute(
                text(
                    """
                    INSERT INTO idempotency_keys (user_id, key, request_hash, expires_at)
                    VALUES ('alice', 'stuck-1', :hash, now() + interval '1 hour')
                    """
                ),
                {"hash": hashlib.sha256(b"original").digest()},
            )
            duplicate = await client.post(
                "/v1/bookings", json=body, headers={"Idempotency-Key": "stuck-1"}
            )
            await txn.rollback()  # the original attempt dies without committing

        assert duplicate.status_code == 409
        assert duplicate.json()["code"] == "REQUEST_IN_PROGRESS"
        retried = await client.post(
            "/v1/bookings", json=body, headers={"Idempotency-Key": "stuck-1"}
        )
        assert retried.status_code == 201
