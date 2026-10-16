from collections.abc import Callable

from redis.asyncio import Redis

from distribook.config import Settings
from distribook.strategies.base import BookingStrategy, ClaimResult
from distribook.strategies.conditional import ConditionalStrategy
from distribook.strategies.naive import NaiveStrategy
from distribook.strategies.pessimistic import PessimisticStrategy
from distribook.strategies.redis_lock import RedisLockStrategy

__all__ = ["BookingStrategy", "ClaimResult", "build_strategy"]


def build_strategy(
    settings: Settings,
    *,
    redis: Redis | None = None,
    on_redis_fallback: Callable[[], None] = lambda: None,
) -> BookingStrategy:
    match settings.booking_strategy:
        case "conditional":
            return ConditionalStrategy()
        case "pessimistic":
            return PessimisticStrategy()
        case "pessimistic_nowait":
            return PessimisticStrategy(nowait=True)
        case "redis_lock":
            if redis is None:
                raise ValueError("the redis_lock strategy needs a Redis client")
            return RedisLockStrategy(
                redis,
                lock_ttl_ms=settings.redis_lock_ttl_ms,
                skip_after_failure_s=settings.redis_skip_after_failure_s,
                on_fallback=on_redis_fallback,
            )
        case "naive":
            return NaiveStrategy()
