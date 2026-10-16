import hashlib

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from distribook.services.maintenance import PURGE_LOCK_ID, purge_expired_idempotency_keys


async def insert_keys(engine: AsyncEngine, *, expired: int, live: int) -> None:
    rows = [(f"expired-{i}", -60.0) for i in range(expired)] + [
        (f"live-{i}", 3600.0) for i in range(live)
    ]
    async with engine.begin() as conn:
        for key, expires_in_s in rows:
            await conn.execute(
                text(
                    """
                    INSERT INTO idempotency_keys (user_id, key, request_hash, expires_at)
                    VALUES ('u', :key, :hash, now() + make_interval(secs => :expires_in_s))
                    """
                ),
                {
                    "key": key,
                    "hash": hashlib.sha256(key.encode()).digest(),
                    "expires_in_s": expires_in_s,
                },
            )


async def remaining_keys(engine: AsyncEngine) -> list[str]:
    async with engine.connect() as conn:
        rows = await conn.execute(text("SELECT key FROM idempotency_keys ORDER BY key"))
        return [row.key for row in rows]


async def test_purge_deletes_only_expired_keys_in_batches(app: FastAPI) -> None:
    engine = app.state.engine
    await insert_keys(engine, expired=7, live=3)

    purged = await purge_expired_idempotency_keys(engine, batch_size=3)

    assert purged == 7
    assert await remaining_keys(engine) == ["live-0", "live-1", "live-2"]


async def test_purge_skips_while_another_replica_holds_the_lock(app: FastAPI) -> None:
    engine = app.state.engine
    await insert_keys(engine, expired=2, live=0)

    async with engine.connect() as other, other.begin():
        await other.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": PURGE_LOCK_ID})
        purged = await purge_expired_idempotency_keys(engine, batch_size=100)

    assert purged == 0
    assert len(await remaining_keys(engine)) == 2
