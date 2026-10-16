"""Fire many requests at the same instant, spread across several API replicas."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# Settings for contention tests:
# - pools large enough that no request is shed, so every outcome is a booking decision;
# - a single attempt per transaction, so a deadlock would surface as an error, not be
#   silently retried;
# - lock_timeout above PostgreSQL's deadlock_timeout (1 s), so a deadlock would be reported as
#   a deadlock rather than hidden behind a lock timeout.
CONTENTION_TUNING: dict[str, Any] = {
    "db_pool_size": 20,
    "db_pool_timeout_s": 30.0,
    "txn_max_attempts": 1,
    "db_lock_timeout_ms": 1500,
    "db_statement_timeout_ms": 3000,
}


CREATED = (201, None)
SEAT_UNAVAILABLE = (409, "SEAT_UNAVAILABLE")
SEAT_CONTENDED = (409, "SEAT_CONTENDED")
SERVICE_OVERLOADED = (503, "SERVICE_OVERLOADED")

# What a request that did not win may receive. SEAT_UNAVAILABLE is the definitive answer. The
# other two are bounded, retryable load shedding: a NOWAIT or Redis rejection, or a lock wait /
# statement that ran past its timeout. Timeouts depend on timing: this harness runs both replicas
# and hundreds of clients on one event loop, so under CPU saturation a winner's transaction can
# hold its lock long enough for some waiters to time out, with any strategy. The tests assert
# correctness strictly and allow these outcomes; how often they occur is a performance property,
# measured by the benchmarks rather than asserted here.
LOSING_OUTCOMES = {SEAT_UNAVAILABLE, SEAT_CONTENDED, SERVICE_OVERLOADED}


@dataclass(frozen=True)
class Call:
    method: str
    url: str
    json: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Outcome:
    call: Call
    status: int
    body: dict[str, Any]

    @property
    def code(self) -> str | None:
        code = self.body.get("code")
        return code if isinstance(code, str) else None

    @property
    def key(self) -> tuple[int, str | None]:
        return self.status, self.code


def booking_call(
    event_id: int, seat_ids: Sequence[int], user_id: str, key: str | None = None
) -> Call:
    return Call(
        "POST",
        "/v1/bookings",
        {"event_id": event_id, "user_id": user_id, "seat_ids": list(seat_ids)},
        {"Idempotency-Key": key or str(uuid4())},
    )


def cancel_call(booking_id: str, user_id: str) -> Call:
    return Call("POST", f"/v1/bookings/{booking_id}/cancel", {"user_id": user_id})


async def fire_simultaneously(
    clients: Sequence[AsyncClient], calls: Sequence[Call]
) -> list[Outcome]:
    """Start every call at the same moment (barrier), round-robin across replicas."""
    barrier = asyncio.Barrier(len(calls))

    async def run(index: int, call: Call) -> Outcome:
        client = clients[index % len(clients)]
        await barrier.wait()
        response = await client.request(call.method, call.url, json=call.json, headers=call.headers)
        return Outcome(call, response.status_code, response.json())

    return list(await asyncio.gather(*(run(i, c) for i, c in enumerate(calls))))


async def active_claims(engine: AsyncEngine, seat_id: int) -> list[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text(
                "SELECT booking_id FROM booking_seats WHERE seat_id = :seat AND released_at IS NULL"
            ),
            {"seat": seat_id},
        )
        return [str(row.booking_id) for row in rows]


async def booked_seats(engine: AsyncEngine, event_id: int) -> set[int]:
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT id FROM seats WHERE event_id = :event AND status = 'BOOKED'"),
            {"event": event_id},
        )
        return {row.id for row in rows}


async def count_rows(engine: AsyncEngine, table: str) -> int:
    async with engine.connect() as conn:
        count = await conn.scalar(text(f"SELECT count(*) FROM {table}"))  # noqa: S608 - test-only constant
    return int(count or 0)
