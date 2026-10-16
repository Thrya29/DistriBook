from typing import Any
from uuid import uuid4

from httpx import AsyncClient, Response


async def create_event(
    client: AsyncClient, *, rows: int = 2, seats_per_row: int = 5, name: str = "Test event"
) -> dict[str, Any]:
    response = await client.post(
        "/v1/events",
        json={
            "name": name,
            "starts_at": "2030-01-01T19:00:00Z",
            "rows": rows,
            "seats_per_row": seats_per_row,
        },
    )
    assert response.status_code == 201, response.text
    event: dict[str, Any] = response.json()
    return event


async def seat_ids(client: AsyncClient, event_id: int) -> list[int]:
    response = await client.get(f"/v1/events/{event_id}/seats", params={"limit": 500})
    assert response.status_code == 200, response.text
    return [seat["id"] for seat in response.json()["items"]]


async def book(
    client: AsyncClient,
    event_id: int,
    seats: list[int],
    *,
    user_id: str = "u-1",
    key: str | None = None,
) -> Response:
    return await client.post(
        "/v1/bookings",
        json={"event_id": event_id, "user_id": user_id, "seat_ids": seats},
        headers={"Idempotency-Key": key or str(uuid4())},
    )


async def seat_status(client: AsyncClient, seat_id: int) -> str:
    response = await client.get(f"/v1/seats/{seat_id}")
    assert response.status_code == 200, response.text
    status: str = response.json()["status"]
    return status
