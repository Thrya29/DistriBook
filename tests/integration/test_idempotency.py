from httpx import AsyncClient

from tests.integration.helpers import book, create_event, seat_ids


async def test_retry_replays_the_original_booking(client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=3)
    seats = await seat_ids(client, event["id"])
    first = await book(client, event["id"], seats[:2], key="retry-1")

    retry = await book(client, event["id"], seats[:2], key="retry-1")

    assert first.status_code == retry.status_code == 201
    assert retry.json() == first.json()
    assert retry.headers["idempotent-replayed"] == "true"
    assert retry.headers["location"] == first.headers["location"]
    fetched = (await client.get(f"/v1/events/{event['id']}")).json()
    assert fetched["seat_counts"]["booked"] == 2  # booked once, not twice


async def test_seat_order_does_not_change_request_identity(client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=3)
    seats = await seat_ids(client, event["id"])
    first = await book(client, event["id"], [seats[0], seats[1]], key="order")

    retry = await book(client, event["id"], [seats[1], seats[0]], key="order")

    assert retry.status_code == 201
    assert retry.json()["id"] == first.json()["id"]


async def test_key_reuse_with_different_body_is_rejected(client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=3)
    seats = await seat_ids(client, event["id"])
    assert (await book(client, event["id"], [seats[0]], key="reused")).status_code == 201

    response = await book(client, event["id"], [seats[1]], key="reused")

    assert response.status_code == 422
    assert response.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert (await client.get(f"/v1/seats/{seats[1]}")).json()["status"] == "AVAILABLE"


async def test_conflict_outcome_is_replayed_deterministically(client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=1)
    seat = (await seat_ids(client, event["id"]))[0]
    winner = (await book(client, event["id"], [seat], user_id="alice")).json()
    lost = await book(client, event["id"], [seat], user_id="bob", key="bob-try")
    assert lost.status_code == 409

    # The seat becomes free again, but a retry of the *same* logical request still reports
    # its original outcome. A new attempt needs a new key.
    cancel = await client.post(f"/v1/bookings/{winner['id']}/cancel", json={"user_id": "alice"})
    assert cancel.status_code == 200
    replay = await book(client, event["id"], [seat], user_id="bob", key="bob-try")
    assert replay.status_code == 409
    assert replay.headers["idempotent-replayed"] == "true"
    assert replay.json() == lost.json()

    fresh = await book(client, event["id"], [seat], user_id="bob", key="bob-try-2")
    assert fresh.status_code == 201


async def test_keys_are_scoped_per_user(client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=2)
    seats = await seat_ids(client, event["id"])

    alice = await book(client, event["id"], [seats[0]], user_id="alice", key="same")
    bob = await book(client, event["id"], [seats[1]], user_id="bob", key="same")

    assert alice.status_code == bob.status_code == 201
    assert alice.json()["id"] != bob.json()["id"]


async def test_validation_failures_do_not_consume_the_key(client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=1)
    seat = (await seat_ids(client, event["id"]))[0]
    assert (await book(client, event["id"], [seat, 999999], key="k1")).status_code == 422

    response = await book(client, event["id"], [seat], key="k1")

    assert response.status_code == 201
