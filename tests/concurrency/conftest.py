from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine

from distribook.config import Settings
from tests.concurrency.harness import CONTENTION_TUNING
from tests.conftest import assert_invariants, client_for, running_app


@dataclass(frozen=True)
class Cluster:
    clients: list[AsyncClient]
    engine: AsyncEngine  # for direct assertions against the database


@pytest.fixture
async def cluster(settings: Settings) -> AsyncIterator[Cluster]:
    """Two API replicas, each with its own connection pool, sharing one database."""
    tuned = settings.model_copy(update=CONTENTION_TUNING)
    async with (
        running_app(tuned) as first,
        running_app(tuned, reset=False) as second,
        client_for(first) as first_client,
        client_for(second) as second_client,
    ):
        yield Cluster([first_client, second_client], first.state.engine)
        await assert_invariants(first.state.engine)
