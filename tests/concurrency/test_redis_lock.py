"""The Redis gate is an optimisation, never a correctness dependency (ADR-0003)."""

import secrets
from collections import Counter
from collections.abc import AsyncIterator

import pytest
from redis.asyncio import Redis

from distribook.config import Settings
from distribook.domain.errors import SeatContended
from distribook.strategies.redis_lock import KEY_PREFIX, RedisLockStrategy
from tests.concurrency.harness import (
    CONTENTION_TUNING,
    CREATED,
    SEAT_CONTENDED,
    SEAT_UNAVAILABLE,
    booking_call,
    fire_simultaneously,
)
from tests.conftest import assert_invariants, client_for, running_app
from tests.integration.helpers import create_event, seat_ids

REDIS_LOCK = {**CONTENTION_TUNING, "booking_strategy": "redis_lock"}


@pytest.fixture
async def redis(settings: Settings) -> AsyncIterator[Redis]:
    client = Redis.from_url(settings.redis_url)
    yield client
    await client.aclose()


def unique_seat_id() -> int:
    # Far outside the ids the database hands out, so these keys never meet real bookings.
    return 10**15 + secrets.randbelow(10**9)


async def test_redis_down_falls_back_to_database_and_stays_correct(settings: Settings) -> None:
    down = settings.model_copy(
        update={**REDIS_LOCK, "redis_url": "redis://127.0.0.1:1/0", "redis_timeout_s": 0.2}
    )
    async with running_app(down) as app, client_for(app) as client:
        ready = await client.get("/readyz")
        assert ready.status_code == 200  # Redis down degrades, it does not take us out
        assert ready.json()["redis"] == "down"
        event = await create_event(client, rows=1, seats_per_row=1)
        seat = (await seat_ids(client, event["id"]))[0]

        outcomes = await fire_simultaneously(
            [client], [booking_call(event["id"], [seat], user_id=f"u{i}") for i in range(100)]
        )

        assert Counter(o.key for o in outcomes) == {CREATED: 1, SEAT_UNAVAILABLE: 99}
        fallbacks = app.state.metrics.registry.get_sample_value("distribook_redis_fallback_total")
        assert fallbacks == 100  # every request took the database-only path
        await assert_invariants(app.state.engine)


async def test_expired_redis_locks_cannot_cause_double_booking(settings: Settings) -> None:
    # A 1 ms lock is effectively always expired, so many requests get past the gate together:
    # the situation a long GC pause or a Redis failover creates. The database must still decide.
    expiring = settings.model_copy(update={**REDIS_LOCK, "redis_lock_ttl_ms": 1})
    async with (
        running_app(expiring) as first,
        running_app(expiring, reset=False) as second,
        client_for(first) as a,
        client_for(second) as b,
    ):
        event = await create_event(a, rows=1, seats_per_row=1)
        seat = (await seat_ids(a, event["id"]))[0]

        outcomes = await fire_simultaneously(
            [a, b], [booking_call(event["id"], [seat], user_id=f"u{i}") for i in range(200)]
        )

        counts = Counter(o.key for o in outcomes)
        assert counts[CREATED] == 1, counts
        assert set(counts) <= {CREATED, SEAT_UNAVAILABLE, SEAT_CONTENDED}, counts
        await assert_invariants(first.state.engine)


async def test_gate_rejects_a_seat_locked_by_someone_else(redis: Redis) -> None:
    seat = unique_seat_id()
    key = f"{KEY_PREFIX}{seat}"
    await redis.set(key, "someone-else", px=5000)
    strategy = RedisLockStrategy(redis, lock_ttl_ms=2000, skip_after_failure_s=5)

    with pytest.raises(SeatContended):
        async with strategy.guard([seat]):
            pytest.fail("the guard must not admit a request while another holds the seat")

    # Compare-and-delete: our failed attempt must not release the other holder's lock.
    assert await redis.get(key) == b"someone-else"
    await redis.delete(key)


async def test_gate_releases_its_locks_and_backs_out_of_partial_acquisition(redis: Redis) -> None:
    free, taken = sorted([unique_seat_id(), unique_seat_id()])
    await redis.set(f"{KEY_PREFIX}{taken}", "someone-else", px=5000)
    strategy = RedisLockStrategy(redis, lock_ttl_ms=2000, skip_after_failure_s=5)

    # Locks are taken in seat order: `free` is acquired first, then `taken` fails; the lock on
    # `free` must not be left behind.
    with pytest.raises(SeatContended):
        async with strategy.guard([taken, free]):
            pass
    assert await redis.exists(f"{KEY_PREFIX}{free}") == 0

    await redis.delete(f"{KEY_PREFIX}{taken}")
    async with strategy.guard([free, taken]):
        assert await redis.exists(f"{KEY_PREFIX}{free}", f"{KEY_PREFIX}{taken}") == 2
    assert await redis.exists(f"{KEY_PREFIX}{free}", f"{KEY_PREFIX}{taken}") == 0
