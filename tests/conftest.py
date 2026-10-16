"""Shared fixtures.

Integration tests run against a real PostgreSQL: concurrency behaviour cannot be verified
against mocks or SQLite. The test database is dropped and recreated once per session, then
migrated with the real Alembic migrations, so the schema under test is the shipped schema.
"""

import asyncio
import os
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import URL, make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine

from distribook.config import Settings
from distribook.invariants import find_violations
from distribook.main import create_app

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEST_DATABASE_URL = (
    "postgresql+asyncpg://distribook:distribook@127.0.0.1:5432/distribook_test"
)
DEFAULT_TEST_REDIS_URL = "redis://127.0.0.1:6380/0"
# Children before parents. DELETE rather than TRUNCATE: tables are tiny between tests, and
# TRUNCATE's file rewrite + fsync is slow on virtualised disks (Docker Desktop).
TABLES_IN_DELETE_ORDER = ("idempotency_keys", "booking_seats", "seats", "bookings", "events")


async def _recreate_database(url: URL) -> None:
    admin_dsn = url.set(drivername="postgresql", database="postgres").render_as_string(
        hide_password=False
    )
    try:
        conn = await asyncpg.connect(admin_dsn, timeout=5)
    except (OSError, asyncpg.PostgresError) as exc:
        pytest.exit(
            f"PostgreSQL is not reachable for integration tests ({exc!r}).\n"
            "Start it with:  docker compose up -d postgres",
            returncode=2,
        )
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{url.database}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{url.database}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def database_url() -> str:
    url = make_url(os.environ.get("DISTRIBOOK_TEST_DATABASE_URL", DEFAULT_TEST_DATABASE_URL))
    if not (url.database or "").endswith("_test"):
        # This database is dropped; never let a misconfigured URL point it at real data.
        pytest.exit("Refusing to run: the test database name must end with '_test'.", returncode=2)
    asyncio.run(_recreate_database(url))

    rendered = url.render_as_string(hide_password=False)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", rendered.replace("%", "%%"))
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    return rendered


@pytest.fixture
def settings(database_url: str) -> Settings:
    return Settings(
        env="test",
        database_url=database_url,
        redis_url=os.environ.get("DISTRIBOOK_TEST_REDIS_URL", DEFAULT_TEST_REDIS_URL),
        log_level="WARNING",
        idempotency_purge_interval_s=0,  # tests run the purge explicitly
    )


async def reset_data(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        for table in TABLES_IN_DELETE_ORDER:
            await conn.execute(text(f"DELETE FROM {table}"))  # noqa: S608 - constant names


async def assert_invariants(engine: AsyncEngine) -> None:
    async with engine.connect() as conn:
        violations = await find_violations(conn)
    assert not violations, f"correctness invariants violated: {violations}"


@asynccontextmanager
async def running_app(settings: Settings, *, reset: bool = True) -> AsyncGenerator[FastAPI]:
    """Start an app with its own connection pool. Several running at once behave like
    separate API replicas sharing one database."""
    application = create_app(settings)
    async with application.router.lifespan_context(application):
        if reset:
            await reset_data(application.state.engine)
        yield application


def client_for(application: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=application), base_url="http://test")


@pytest.fixture
async def app(settings: Settings) -> AsyncIterator[FastAPI]:
    async with running_app(settings) as application:
        yield application
        # Every integration test doubles as an invariant check on the state it left behind.
        await assert_invariants(application.state.engine)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    async with client_for(app) as http:
        yield http
