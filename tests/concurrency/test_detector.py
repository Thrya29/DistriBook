"""Proof that the contention tests can detect a race.

A test that passes against the safe strategy only means something if the same test fails
against an unsafe one. These tests run the hot-seat scenario against the deliberately broken
``naive`` strategy (check, then write) in two configurations:

1. with the database safety net (partial unique index) in place: the index catches the race,
   so there is still exactly one winner; this is defence in depth working;
2. with the safety net removed: the detector must find duplicate bookings.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from distribook.config import Settings
from distribook.invariants import find_violations
from distribook.services.booking_service import ONE_ACTIVE_CLAIM_INDEX, BookingService
from distribook.strategies.naive import NaiveStrategy
from tests.concurrency.harness import (
    CONTENTION_TUNING,
    CREATED,
    LOSING_OUTCOMES,
    booking_call,
    fire_simultaneously,
)
from tests.conftest import assert_invariants, client_for, reset_data, running_app
from tests.integration.helpers import create_event, seat_ids

CONTENDERS = 100
RACE_WINDOW_S = 0.05  # widens check-to-write so the race reproduces on every run
NAIVE: dict[str, Any] = {
    **CONTENTION_TUNING,
    "booking_strategy": "naive",
    "enable_unsafe_strategies": True,
}
CREATE_SAFETY_NET = (
    f"CREATE UNIQUE INDEX {ONE_ACTIVE_CLAIM_INDEX} "
    "ON booking_seats (seat_id) WHERE released_at IS NULL"
)


@asynccontextmanager
async def naive_replicas(
    settings: Settings,
) -> AsyncGenerator[tuple[list[AsyncClient], AsyncEngine]]:
    tuned = Settings.model_validate({**settings.model_dump(), **NAIVE})
    async with running_app(tuned) as first, running_app(tuned, reset=False) as second:
        for app in (first, second):
            _use_naive_with_race_window(app, tuned)
        async with client_for(first) as a, client_for(second) as b:
            yield [a, b], first.state.engine


def _use_naive_with_race_window(app: FastAPI, settings: Settings) -> None:
    app.state.bookings = BookingService(
        app.state.engine, NaiveStrategy(race_window_s=RACE_WINDOW_S), settings, app.state.metrics
    )


async def _hot_seat(clients: list[AsyncClient]) -> int:
    event = await create_event(clients[0], rows=1, seats_per_row=1)
    seat = (await seat_ids(clients[0], event["id"]))[0]
    outcomes = await fire_simultaneously(
        clients, [booking_call(event["id"], [seat], user_id=f"user-{i}") for i in range(CONTENDERS)]
    )
    assert {o.key for o in outcomes} <= {CREATED} | LOSING_OUTCOMES
    return sum(o.status == 201 for o in outcomes)


async def test_safety_net_index_stops_the_naive_race(settings: Settings) -> None:
    async with naive_replicas(settings) as (clients, engine):
        winners = await _hot_seat(clients)

        assert winners == 1
        await assert_invariants(engine)


async def test_detector_catches_naive_race_without_safety_net(settings: Settings) -> None:
    async with naive_replicas(settings) as (clients, engine):
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP INDEX {ONE_ACTIVE_CLAIM_INDEX}"))
        try:
            winners = await _hot_seat(clients)
            async with engine.connect() as conn:
                violations = await find_violations(conn)

            assert winners > 1, "the unsafe strategy should have double-booked"
            assert "INV-1 a seat has at most one active claim" in violations
        finally:
            # Restore the shared test schema: clear the corrupt rows, then rebuild the index.
            await reset_data(engine)
            async with engine.begin() as conn:
                await conn.execute(text(CREATE_SAFETY_NET))
