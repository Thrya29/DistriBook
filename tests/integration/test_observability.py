from fastapi import FastAPI
from httpx import AsyncClient

from tests.integration.helpers import book, create_event, seat_ids


def sample(app: FastAPI, name: str, **labels: str) -> float | None:
    value = app.state.metrics.registry.get_sample_value(name, labels)
    return float(value) if value is not None else None


async def test_booking_outcomes_are_counted_per_strategy(app: FastAPI, client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=1)
    seat = (await seat_ids(client, event["id"]))[0]
    await book(client, event["id"], [seat], user_id="a", key="k-a")
    await book(client, event["id"], [seat], user_id="a", key="k-a")  # replay
    await book(client, event["id"], [seat], user_id="b")  # conflict

    attempts = "distribook_booking_attempts_total"
    assert sample(app, attempts, strategy="conditional", outcome="created") == 1
    assert sample(app, attempts, strategy="conditional", outcome="replayed") == 1
    assert sample(app, attempts, strategy="conditional", outcome="seat_unavailable") == 1


async def test_latency_histogram_uses_route_templates(app: FastAPI, client: AsyncClient) -> None:
    event = await create_event(client, rows=1, seats_per_row=1)
    await client.get(f"/v1/events/{event['id']}")
    await client.get("/no/such/path")

    histogram = "http_request_duration_seconds_count"
    template = {"route": "/v1/events/{event_id}", "method": "GET", "status": "200"}
    assert sample(app, histogram, **template) == 1
    # Unknown paths share one series instead of creating one series per path.
    assert sample(app, histogram, route="unmatched", method="GET", status="404") == 1


async def test_metrics_endpoint_exposes_prometheus_text(client: AsyncClient) -> None:
    await client.get("/healthz")
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "http_request_duration_seconds_bucket" in body
    assert "distribook_db_pool_size 10.0" in body
    assert "distribook_db_pool_in_use" in body


async def test_request_id_is_echoed_or_generated(client: AsyncClient) -> None:
    echoed = await client.get("/healthz", headers={"X-Request-ID": "trace-abc-123"})
    assert echoed.headers["x-request-id"] == "trace-abc-123"

    generated = await client.get("/healthz")
    assert len(generated.headers["x-request-id"]) == 32

    # Header values that could break log lines are replaced, not echoed.
    replaced = await client.get("/healthz", headers={"X-Request-ID": "bad value with spaces"})
    assert replaced.headers["x-request-id"] != "bad value with spaces"
