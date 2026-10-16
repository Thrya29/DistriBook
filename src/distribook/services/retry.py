import asyncio
import random
from collections.abc import Awaitable, Callable

from sqlalchemy.exc import DBAPIError

from distribook.db.errors import DEADLOCK_DETECTED, RETRYABLE_SQLSTATES, sqlstate

_REASONS = {DEADLOCK_DETECTED: "deadlock"}


async def run_with_retries[T](
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int,
    on_retry: Callable[[str], None] = lambda reason: None,
) -> T:
    """Re-run a whole transaction when PostgreSQL aborted it with a deadlock or serialization
    failure. Lock ordering should make this unreachable; it is a safety net, not a mechanism,
    and ``on_retry`` lets metrics show whether it ever fires."""
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except DBAPIError as exc:
            state = sqlstate(exc)
            if state not in RETRYABLE_SQLSTATES or attempt == attempts:
                raise
            on_retry(_REASONS.get(state, "serialization_failure"))
        # Full jitter: spread retries out so they do not collide again in lock step.
        await asyncio.sleep(random.uniform(0, 0.005 * 2**attempt))  # noqa: S311 - not crypto
    raise AssertionError("unreachable")
