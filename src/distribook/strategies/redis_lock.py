"""Redis-gated booking (ADR-0003): an optimisation, never a correctness dependency.

Per-seat Redis locks are taken *before* a database connection is used, so under heavy
contention the losers are turned away without touching the connection pool. The database step
is the same conditional write as ``conditional``; that write, not the Redis lock, decides who
wins. An expired lock or a Redis failover can let two requests through, and the conditional
write still picks exactly one.
"""

import logging
import secrets
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncConnection

from distribook.domain.errors import SeatContended
from distribook.strategies.base import BookingStrategy, ClaimResult
from distribook.strategies.conditional import ConditionalStrategy

logger = logging.getLogger(__name__)

KEY_PREFIX = "distribook:lock:seat:"

# Delete each key only if it still holds our token, so a request whose lock already expired
# never releases a lock that now belongs to someone else.
_RELEASE = """
local released = 0
for i, key in ipairs(KEYS) do
    if redis.call('GET', key) == ARGV[1] then
        released = released + redis.call('DEL', key)
    end
end
return released
"""


class RedisLockStrategy(BookingStrategy):
    name = "redis_lock"

    def __init__(
        self,
        redis: Redis,
        *,
        lock_ttl_ms: int,
        skip_after_failure_s: float,
        on_fallback: Callable[[], None] = lambda: None,
    ) -> None:
        self._redis = redis
        self._lock_ttl_ms = lock_ttl_ms
        self._skip_after_failure_s = skip_after_failure_s
        self._on_fallback = on_fallback
        self._skip_until = 0.0  # circuit breaker: after a failure, stop paying Redis timeouts
        self._database_step = ConditionalStrategy()

    @asynccontextmanager
    async def guard(self, seat_ids: Sequence[int]) -> AsyncIterator[None]:
        token = secrets.token_hex(16)
        held: list[str] = []
        try:
            if time.monotonic() < self._skip_until:
                self._on_fallback()
            else:
                try:
                    for seat_id in sorted(seat_ids):  # same global order as the row locks
                        key = f"{KEY_PREFIX}{seat_id}"
                        if not await self._redis.set(key, token, nx=True, px=self._lock_ttl_ms):
                            raise SeatContended()
                        held.append(key)
                except RedisError as exc:
                    # Fall back to the database-only path; correctness is unaffected.
                    self._skip_until = time.monotonic() + self._skip_after_failure_s
                    self._on_fallback()
                    logger.warning("redis unavailable, using database-only path: %r", exc)
            yield
        finally:
            await self._release(held, token)

    async def _release(self, keys: list[str], token: str) -> None:
        if not keys:
            return
        try:
            await self._redis.eval(_RELEASE, len(keys), *keys, token)
        except RedisError as exc:
            # The locks expire on their own after lock_ttl_ms.
            logger.warning("could not release redis locks: %r", exc)

    async def claim(
        self, conn: AsyncConnection, *, event_id: int, seat_ids: Sequence[int], booking_id: UUID
    ) -> ClaimResult:
        return await self._database_step.claim(
            conn, event_id=event_id, seat_ids=seat_ids, booking_id=booking_id
        )
