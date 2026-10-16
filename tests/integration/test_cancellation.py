from httpx import AsyncClient

from tests.integration.helpers import book, create_event, seat_ids, seat_status


async def test_cancel_releases_seats(client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=3)
    seats = await seat_ids(client, event["id"])
    booking = (await book(client, event["id"], seats[:2], user_id="alice")).json()

    response = await client.post(f"/v1/bookings/{booking['id']}/cancel", json={"user_id": "alice"})

    assert response.status_code == 200
    cancelled = response.json()
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["cancelled_at"] is not None
    assert cancelled["seats"] == booking["seats"]  # history is kept
    assert [await seat_status(client, s) for s in seats[:2]] == ["AVAILABLE", "AVAILABLE"]


async def test_released_seat_can_be_booked_again(client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=1)
    seat = (await seat_ids(client, event["id"]))[0]
    first = (await book(client, event["id"], [seat], user_id="alice")).json()
    await client.post(f"/v1/bookings/{first['id']}/cancel", json={"user_id": "alice"})

    second = await book(client, event["id"], [seat], user_id="bob")

    assert second.status_code == 201
    assert second.json()["id"] != first["id"]


async def test_cancel_is_idempotent(client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=1)
    seat = (await seat_ids(client, event["id"]))[0]
    booking = (await book(client, event["id"], [seat], user_id="alice")).json()
    url = f"/v1/bookings/{booking['id']}/cancel"

    first = await client.post(url, json={"user_id": "alice"})
    again = await client.post(url, json={"user_id": "alice"})

    assert first.status_code == again.status_code == 200
    assert again.json() == first.json()


async def test_cancel_after_rebooking_does_not_touch_new_owner(client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=1)
    seat = (await seat_ids(client, event["id"]))[0]
    first = (await book(client, event["id"], [seat], user_id="alice")).json()
    url = f"/v1/bookings/{first['id']}/cancel"
    await client.post(url, json={"user_id": "alice"})
    assert (await book(client, event["id"], [seat], user_id="bob")).status_code == 201

    await client.post(url, json={"user_id": "alice"})  # repeated cancel of the old booking

    assert await seat_status(client, seat) == "BOOKED"


async def test_cancel_by_another_user_is_not_found(client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=1)
    seat = (await seat_ids(client, event["id"]))[0]
    booking = (await book(client, event["id"], [seat], user_id="alice")).json()

    response = await client.post(f"/v1/bookings/{booking['id']}/cancel", json={"user_id": "eve"})

    assert response.status_code == 404
    assert response.json()["code"] == "BOOKING_NOT_FOUND"
    assert await seat_status(client, seat) == "BOOKED"


async def test_cancel_unknown_booking(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/bookings/00000000-0000-4000-8000-000000000000/cancel", json={"user_id": "u"}
    )
    assert response.status_code == 404
