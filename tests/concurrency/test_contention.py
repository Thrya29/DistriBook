"""Many simultaneous requests across two replicas. Each test also ends with the invariant check
(see the ``cluster`` fixture), so "no double booking" is verified in the database itself."""

import random
from collections import Counter
from itertools import combinations

from tests.concurrency.conftest import Cluster
from tests.concurrency.harness import (
    CREATED,
    LOSING_OUTCOMES,
    SEAT_CONTENDED,
    SEAT_UNAVAILABLE,
    active_claims,
    booked_seats,
    booking_call,
    cancel_call,
    count_rows,
    fire_simultaneously,
)
from tests.integration.helpers import book, create_event, seat_ids


async def test_hot_seat_has_exactly_one_winner(cluster: Cluster) -> None:
    contenders = 500
    event = await create_event(cluster.clients[0], rows=1, seats_per_row=1)
    seat = (await seat_ids(cluster.clients[0], event["id"]))[0]

    outcomes = await fire_simultaneously(
        cluster.clients,
        [booking_call(event["id"], [seat], user_id=f"user-{i}") for i in range(contenders)],
    )

    counts = Counter(o.key for o in outcomes)
    assert counts[CREATED] == 1, counts
    assert set(counts) <= {CREATED} | LOSING_OUTCOMES, counts
    winner = next(o for o in outcomes if o.status == 201)
    assert await active_claims(cluster.engine, seat) == [winner.body["id"]]
    assert await count_rows(cluster.engine, "bookings") == 1


async def test_independent_seats_all_succeed(cluster: Cluster) -> None:
    event = await create_event(cluster.clients[0], rows=10, seats_per_row=20)
    seats = await seat_ids(cluster.clients[0], event["id"])

    outcomes = await fire_simultaneously(
        cluster.clients,
        [booking_call(event["id"], [seat], user_id=f"user-{seat}") for seat in seats],
    )

    assert Counter(o.key for o in outcomes) == {CREATED: len(seats)}
    assert await booked_seats(cluster.engine, event["id"]) == set(seats)


async def test_overlapping_multi_seat_bookings_are_atomic_and_deadlock_free(
    cluster: Cluster,
) -> None:
    # Requests overlap heavily and list their seats in random order, so a strategy that locked
    # seats in request order could deadlock here. With txn_max_attempts=1 a deadlock would
    # surface as a 500 instead of being silently retried, so "no 500" means "no deadlock".
    rng = random.Random(20261016)
    event = await create_event(cluster.clients[0], rows=1, seats_per_row=12)
    seats = await seat_ids(cluster.clients[0], event["id"])
    calls = [
        booking_call(event["id"], rng.sample(seats, k=rng.randint(2, 4)), user_id=f"user-{i}")
        for i in range(300)
    ]

    outcomes = await fire_simultaneously(cluster.clients, calls)

    counts = Counter(o.key for o in outcomes)
    assert set(counts) <= {CREATED} | LOSING_OUTCOMES, counts
    winners = [set(o.call.json["seat_ids"]) for o in outcomes if o.status == 201]
    assert winners, "at least one booking must succeed"
    for a, b in combinations(winners, 2):
        assert a.isdisjoint(b), "two confirmed bookings share a seat"
    # All or nothing: exactly the winners' seats are booked; losers left no trace.
    assert await booked_seats(cluster.engine, event["id"]) == set().union(*winners)
    for loser in (o for o in outcomes if o.key == SEAT_UNAVAILABLE):
        assert set(loser.body["unavailable_seat_ids"]) <= set(loser.call.json["seat_ids"])


async def test_simultaneous_retries_with_one_key_book_once(cluster: Cluster) -> None:
    event = await create_event(cluster.clients[0], rows=1, seats_per_row=1)
    seat = (await seat_ids(cluster.clients[0], event["id"]))[0]
    call = booking_call(event["id"], [seat], user_id="alice", key="retry-storm")

    outcomes = await fire_simultaneously(cluster.clients, [call] * 50)

    counts = Counter(o.key for o in outcomes)
    # The Redis gate turns a duplicate away before it reaches the database, so it cannot tell
    # a replay from a competitor; it answers with a retryable 409 and the retry replays.
    allowed = {CREATED, SEAT_CONTENDED} if cluster.strategy == "redis_lock" else {CREATED}
    assert set(counts) <= allowed and counts[CREATED] >= 1, counts
    assert len({o.body["id"] for o in outcomes if o.status == 201}) == 1
    assert await count_rows(cluster.engine, "bookings") == 1


async def test_one_key_with_conflicting_bodies_books_at_most_once(cluster: Cluster) -> None:
    event = await create_event(cluster.clients[0], rows=1, seats_per_row=30)
    seats = await seat_ids(cluster.clients[0], event["id"])
    calls = [booking_call(event["id"], [seat], user_id="alice", key="shared") for seat in seats]

    outcomes = await fire_simultaneously(cluster.clients, calls)

    assert Counter(o.key for o in outcomes) == {
        CREATED: 1,
        (422, "IDEMPOTENCY_KEY_REUSED"): len(seats) - 1,
    }
    assert len(await booked_seats(cluster.engine, event["id"])) == 1


async def test_cancel_racing_new_bookings_hands_seat_to_at_most_one(cluster: Cluster) -> None:
    client = cluster.clients[0]
    event = await create_event(client, rows=1, seats_per_row=1)
    seat = (await seat_ids(client, event["id"]))[0]

    for round_no in range(15):
        owner = f"owner-{round_no}"
        current = await book(client, event["id"], [seat], user_id=owner)
        assert current.status_code == 201, current.text
        calls = [cancel_call(current.json()["id"], owner)] + [
            booking_call(event["id"], [seat], user_id=f"r{round_no}-u{i}") for i in range(30)
        ]

        outcomes = await fire_simultaneously(cluster.clients, calls)

        cancel, bookings = outcomes[0], outcomes[1:]
        assert cancel.status == 200 and cancel.body["status"] == "CANCELLED"
        assert {o.key for o in bookings} <= {CREATED} | LOSING_OUTCOMES
        winners = [o.body["id"] for o in bookings if o.status == 201]
        assert len(winners) <= 1
        claims = await active_claims(cluster.engine, seat)
        assert claims == winners
        if winners:  # free the seat for the next round
            winner_user = next(o.call.json["user_id"] for o in bookings if o.status == 201)
            freed = await client.post(
                f"/v1/bookings/{winners[0]}/cancel", json={"user_id": winner_user}
            )
            assert freed.status_code == 200
