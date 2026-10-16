import pytest
from httpx import AsyncClient

from tests.integration.helpers import book, create_event, seat_ids


async def test_create_event_generates_seat_grid(client: AsyncClient) -> None:
    event = await create_event(client, rows=3, seats_per_row=4, name="Concert")

    assert event["name"] == "Concert"
    assert event["seat_counts"] == {"total": 12, "available": 12, "booked": 0}
    page = (await client.get(f"/v1/events/{event['id']}/seats", params={"limit": 500})).json()
    labels = [seat["label"] for seat in page["items"]]
    assert labels[:5] == ["A1", "A2", "A3", "A4", "B1"]
    assert labels[-1] == "C4"
    assert page["next_after_id"] is None


async def test_create_event_sets_location(client: AsyncClient) -> None:
    response = await client.post(
        "/v1/events",
        json={"name": "x", "starts_at": "2030-01-01T00:00:00+05:30", "rows": 1, "seats_per_row": 1},
    )
    assert response.status_code == 201
    assert response.headers["location"] == f"/v1/events/{response.json()['id']}"


@pytest.mark.parametrize(
    "body",
    [
        {"name": "x", "starts_at": "2030-01-01T00:00:00Z", "rows": 0, "seats_per_row": 1},
        {"name": "x", "starts_at": "2030-01-01T00:00:00Z", "rows": 101, "seats_per_row": 1},
        {"name": "", "starts_at": "2030-01-01T00:00:00Z", "rows": 1, "seats_per_row": 1},
        {"name": "x", "starts_at": "2030-01-01T00:00:00", "rows": 1, "seats_per_row": 1},
        {"name": "x", "starts_at": "2030-01-01T00:00:00Z", "rows": 1, "seats_per_row": 1, "x": 1},
    ],
)
async def test_create_event_validation(client: AsyncClient, body: dict[str, object]) -> None:
    response = await client.post("/v1/events", json=body)
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "VALIDATION_ERROR"


async def test_get_event_counts_reflect_bookings(client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=5)
    seats = await seat_ids(client, event["id"])
    assert (await book(client, event["id"], seats[:2])).status_code == 201

    fetched = (await client.get(f"/v1/events/{event['id']}")).json()
    assert fetched["seat_counts"] == {"total": 5, "available": 3, "booked": 2}


async def test_get_unknown_event(client: AsyncClient) -> None:
    response = await client.get("/v1/events/999999")
    assert response.status_code == 404
    assert response.json()["code"] == "EVENT_NOT_FOUND"


async def test_seat_listing_paginates_with_keyset(client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=7)
    collected: list[int] = []
    after_id = 0
    for _ in range(10):
        page = (
            await client.get(
                f"/v1/events/{event['id']}/seats", params={"limit": 3, "after_id": after_id}
            )
        ).json()
        collected += [seat["id"] for seat in page["items"]]
        if page["next_after_id"] is None:
            break
        after_id = page["next_after_id"]
    assert collected == sorted(collected)
    assert len(collected) == len(set(collected)) == 7


async def test_seat_listing_filters_by_status(client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=4)
    seats = await seat_ids(client, event["id"])
    assert (await book(client, event["id"], [seats[1]])).status_code == 201

    url = f"/v1/events/{event['id']}/seats"
    booked = (await client.get(url, params={"status": "BOOKED"})).json()["items"]
    available = (await client.get(url, params={"status": "AVAILABLE"})).json()["items"]
    assert [s["id"] for s in booked] == [seats[1]]
    assert [s["id"] for s in available] == [seats[0], seats[2], seats[3]]


async def test_seat_listing_rejects_unknown_status(client: AsyncClient) -> None:
    event = await create_event(client)
    response = await client.get(f"/v1/events/{event['id']}/seats", params={"status": "HELD"})
    assert response.status_code == 422


async def test_seat_listing_unknown_event(client: AsyncClient) -> None:
    assert (await client.get("/v1/events/424242/seats")).status_code == 404


async def test_get_seat(client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=1)
    seat_id = (await seat_ids(client, event["id"]))[0]
    seat = (await client.get(f"/v1/seats/{seat_id}")).json()
    assert seat == {"id": seat_id, "event_id": event["id"], "label": "A1", "status": "AVAILABLE"}
    assert (await client.get("/v1/seats/999999")).json()["code"] == "SEAT_NOT_FOUND"
