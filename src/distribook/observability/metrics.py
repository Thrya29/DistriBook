"""Prometheus metrics. Each app owns its registry, so several app instances (tests, or
replicas in one process) never collide."""

from typing import cast

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import QueuePool

# Fine resolution below 100 ms, where a healthy booking lands; coarse above it.
LATENCY_BUCKETS = (
    0.001, 0.0025, 0.005, 0.0075, 0.01, 0.015, 0.025, 0.05, 0.075,
    0.1, 0.25, 0.5, 1.0, 2.5, 5.0,
)  # fmt: skip


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.http_request_duration = Histogram(
            "http_request_duration_seconds",
            "HTTP request latency by route template.",
            ["route", "method", "status"],
            buckets=LATENCY_BUCKETS,
            registry=self.registry,
        )
        self.booking_attempts = Counter(
            "distribook_booking_attempts",
            "Booking requests by strategy and outcome.",
            ["strategy", "outcome"],
            registry=self.registry,
        )
        self.txn_retries = Counter(
            "distribook_txn_retries",
            "Transactions re-run after a deadlock or serialization failure. Expected to stay 0.",
            ["reason"],
            registry=self.registry,
        )
        self.redis_fallbacks = Counter(
            "distribook_redis_fallback",
            "Bookings that skipped the Redis lock because Redis was unavailable.",
            registry=self.registry,
        )
        self.idempotency_keys_purged = Counter(
            "distribook_idempotency_keys_purged",
            "Expired idempotency keys deleted by the background job.",
            registry=self.registry,
        )
        self.db_pool_in_use = Gauge(
            "distribook_db_pool_in_use",
            "Database connections currently checked out.",
            registry=self.registry,
        )
        self.db_pool_size = Gauge(
            "distribook_db_pool_size", "Configured connection pool size.", registry=self.registry
        )

    def bind_pool(self, engine: AsyncEngine) -> None:
        pool = cast(QueuePool, engine.pool)  # asyncpg engines use a QueuePool subclass
        self.db_pool_in_use.set_function(pool.checkedout)
        self.db_pool_size.set_function(pool.size)

    def render(self) -> tuple[bytes, str]:
        return generate_latest(self.registry), CONTENT_TYPE_LATEST
