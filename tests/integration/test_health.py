from typing import cast

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.pool import QueuePool


async def test_liveness(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_readiness_checks_database(client: AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "up"}


async def test_unknown_route_is_problem_json(client: AsyncClient) -> None:
    response = await client.get("/v1/nope")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "HTTP_404"


async def test_connection_pool_is_warm_after_startup(app: FastAPI) -> None:
    pool = cast(QueuePool, app.state.engine.pool)
    assert pool.checkedin() == app.state.settings.db_pool_size
