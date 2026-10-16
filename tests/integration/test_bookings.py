import pytest
from httpx import AsyncClient

from tests.integration.helpers import book, create_event, seat_ids, seat_status


async def test_book_single_seat(client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=3)
    seats = await seat_ids(client, event["id"])

    response = await book(client, event["id"], [seats[0]], user_id="alice")

    assert response.status_code == 201
    booking = response.json()
    assert response.headers["location"] == f"/v1/bookings/{booking['id']}"
    assert "idempotent-replayed" not in response.headers
    assert booking["status"] == "CONFIRMED"
    assert booking["user_id"] == "alice"
    assert booking["seats"] == [{"id": seats[0], "label": "A1"}]
    assert booking["cancelled_at"] is None
    assert await seat_status(client, seats[0]) == "BOOKED"


async def test_book_multiple_seats_returns_them_sorted(client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=5)
    seats = await seat_ids(client, event["id"])

    response = await book(client, event["id"], [seats[3], seats[1]])

    assert response.status_code == 201
    assert [s["id"] for s in response.json()["seats"]] == [seats[1], seats[3]]


async def test_booked_seat_conflicts(client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=2)
    seats = await seat_ids(client, event["id"])
    assert (await book(client, event["id"], [seats[0]], user_id="alice")).status_code == 201

    response = await book(client, event["id"], [seats[0]], user_id="bob")

    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    problem = response.json()
    assert problem["code"] == "SEAT_UNAVAILABLE"
    assert problem["unavailable_seat_ids"] == [seats[0]]


async def test_multi_seat_booking_is_all_or_nothing(client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=4)
    seats = await seat_ids(client, event["id"])
    assert (await book(client, event["id"], [seats[2]])).status_code == 201

    response = await book(client, event["id"], [seats[0], seats[1], seats[2]])

    assert response.status_code == 409
    assert response.json()["unavailable_seat_ids"] == [seats[2]]
    # The seats that were free must not have been claimed by the failed request.
    assert await seat_status(client, seats[0]) == "AVAILABLE"
    assert await seat_status(client, seats[1]) == "AVAILABLE"


async def test_unknown_event(client: AsyncClient) -> None:
    response = await book(client, 999999, [1])
    assert response.status_code == 404
    assert response.json()["code"] == "EVENT_NOT_FOUND"


async def test_seats_from_another_event_are_rejected(client: AsyncClient) -> None:
    first = await create_event(client, rows=1, seats_per_row=2)
    second = await create_event(client, rows=1, seats_per_row=2)
    foreign = (await seat_ids(client, second["id"]))[0]
    own = (await seat_ids(client, first["id"]))[0]

    response = await book(client, first["id"], [own, foreign])

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_SEATS"
    assert response.json()["invalid_seat_ids"] == [foreign]
    assert await seat_status(client, own) == "AVAILABLE"


async def test_missing_idempotency_key(client: AsyncClient) -> None:
    event = await create_event(client)
    response = await client.post(
        "/v1/bookings", json={"event_id": event["id"], "user_id": "u", "seat_ids": [1]}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "IDEMPOTENCY_KEY_MISSING"


async def test_malformed_idempotency_key(client: AsyncClient) -> None:
    event = await create_event(client)
    response = await book(client, event["id"], [1], key="has space")
    assert response.status_code == 400
    assert response.json()["code"] == "IDEMPOTENCY_KEY_INVALID"


@pytest.mark.parametrize(
    "body",
    [
        {"event_id": 1, "user_id": "u", "seat_ids": []},
        {"event_id": 1, "user_id": "u", "seat_ids": list(range(1, 12))},
        {"event_id": 1, "user_id": "u", "seat_ids": [1, 1]},
        {"event_id": 1, "user_id": "", "seat_ids": [1]},
        {"event_id": 1, "seat_ids": [1]},
        {"event_id": "one", "user_id": "u", "seat_ids": [1]},
    ],
)
async def test_booking_validation(client: AsyncClient, body: dict[str, object]) -> None:
    response = await client.post("/v1/bookings", json=body, headers={"Idempotency-Key": "k"})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_get_booking(client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=2)
    seats = await seat_ids(client, event["id"])
    created = (await book(client, event["id"], seats)).json()

    fetched = await client.get(f"/v1/bookings/{created['id']}")

    assert fetched.status_code == 200
    assert fetched.json() == created


async def test_get_unknown_booking(client: AsyncClient) -> None:
    response = await client.get("/v1/bookings/00000000-0000-4000-8000-000000000000")
    assert response.status_code == 404
    assert response.json()["code"] == "BOOKING_NOT_FOUND"


async def test_get_booking_with_malformed_id(client: AsyncClient) -> None:
    assert (await client.get("/v1/bookings/not-a-uuid")).status_code == 422
