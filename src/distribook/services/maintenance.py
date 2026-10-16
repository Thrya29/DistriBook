"""Background housekeeping that every replica runs but only one executes at a time."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# Arbitrary application-wide advisory lock id ("DBPURGE" in ASCII).
PURGE_LOCK_ID = 0x4442_5055_5247_45

_PURGE_BATCH = text(
    """
    DELETE FROM idempotency_keys k
    USING (
        SELECT user_id, key FROM idempotency_keys
        WHERE expires_at <= now()
        LIMIT :batch_size
        FOR UPDATE SKIP LOCKED
    ) expired
    WHERE k.user_id = expired.user_id AND k.key = expired.key
    """
)


async def purge_expired_idempotency_keys(engine: AsyncEngine, *, batch_size: int) -> int:
    """Delete expired keys in small batches; returns how many were deleted.

    Each batch is its own short transaction holding a transaction-level advisory lock, so only
    one replica purges at a time, nothing is left locked if a replica dies, and live booking
    traffic is never blocked for long. SKIP LOCKED leaves alone any row a request is recycling.
    """
    total = 0
    while True:
        async with engine.connect() as conn, conn.begin():
            if not await conn.scalar(
                text("SELECT pg_try_advisory_xact_lock(:id)"), {"id": PURGE_LOCK_ID}
            ):
                return total  # another replica is purging
            deleted = (await conn.execute(_PURGE_BATCH, {"batch_size": batch_size})).rowcount
        total += deleted
        if deleted < batch_size:
            return total


async def run_periodically(interval_s: float, job: Callable[[], Awaitable[object]]) -> None:
    """Run ``job`` every ``interval_s`` until cancelled. A failing run is logged, not fatal."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            await job()
        except Exception:
            logger.exception("periodic job failed")
