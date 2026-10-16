import asyncio
import random
from collections.abc import Awaitable, Callable

from sqlalchemy.exc import DBAPIError

from distribook.db.errors import RETRYABLE_SQLSTATES, sqlstate


async def run_with_retries[T](operation: Callable[[], Awaitable[T]], *, attempts: int) -> T:
    """Re-run a whole transaction when PostgreSQL aborted it with a deadlock or serialization
    failure. Lock ordering should make this unreachable; it is a safety net, not a mechanism."""
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except DBAPIError as exc:
            if sqlstate(exc) not in RETRYABLE_SQLSTATES or attempt == attempts:
                raise
        # Full jitter: spread retries out so they do not collide again in lock step.
        await asyncio.sleep(random.uniform(0, 0.005 * 2**attempt))  # noqa: S311 - not crypto
    raise AssertionError("unreachable")
