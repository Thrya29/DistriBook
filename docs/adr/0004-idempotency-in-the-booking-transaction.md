# ADR-0004: Idempotency keys are recorded inside the booking transaction

**Status:** Accepted · **Date:** 2026-10-16

## Context

Clients retry after timeouts and dropped connections. Without idempotency, a retry after a
successful-but-unacknowledged booking would either double-book or wrongly report a conflict. The
mechanism must also handle the *same* request arriving twice at the same moment.

## Decision

- `POST /v1/bookings` requires an `Idempotency-Key` header. Keys are scoped by `user_id` and kept for 24 h.
- In the same transaction as the booking: `INSERT INTO idempotency_keys … ON CONFLICT DO NOTHING`
  (see "Lesson from testing" below for why not `DO UPDATE`).
  - Inserted: this request owns the key and goes ahead.
  - Conflict: PostgreSQL has already made us wait until the transaction that owns the key finished.
    We read its stored response and replay it, or return `422 IDEMPOTENCY_KEY_REUSED` if the
    SHA-256 of the canonical request body differs.
- Seats are claimed inside a `SAVEPOINT`, so a lost race rolls back the booking but still commits
  the final `409 SEAT_UNAVAILABLE` against the key.
- Temporary outcomes (`SEAT_CONTENDED`, `503`, `5xx`) are not stored. A retry gets a fresh attempt.
- Expired keys are deleted by a periodic job. `pg_try_advisory_lock` makes sure only one replica runs it.

## Consequences

- No separate `IN_PROGRESS` state, no leftover locks, no background repair. A crash rolls back the key
  together with the booking.
- A duplicate that arrives while the original is still running waits for it (bounded by
  `lock_timeout`) instead of getting a `409` it would have to retry.
- Replaying requires the stored response body; this costs a small amount of storage per booking.

## Lesson from testing: `DO NOTHING`, not `DO UPDATE … WHERE`

The first implementation recycled expired keys in the same statement:
`ON CONFLICT … DO UPDATE … WHERE expires_at <= now()`. The concurrency suite (50 simultaneous
retries with one key) failed intermittently: a few duplicates got `409 REQUEST_IN_PROGRESS`
instead of a replay. No booking was ever duplicated.

Cause: `ON CONFLICT DO UPDATE` **locks the conflicting row even when its `WHERE` is false**.
Each duplicate therefore held the key row's lock until *its own* transaction finished, so the
duplicates queued behind one another (a lock convoy) instead of waiting only for the original,
and the end of the queue went past `lock_timeout`.

Fix: `ON CONFLICT DO NOTHING`, which waits for an in-flight original but takes no row lock,
followed (only when no row was inserted) by a plain conditional
`UPDATE … WHERE expires_at <= now()`. A plain `UPDATE` does not lock rows whose `WHERE` is false,
so for a live key this step is lock-free. After the fix the same test passed in repeated runs,
and the contention suite ran about a third faster.

## Alternatives considered

- **Key stored in Redis:** a second system whose state could disagree with the booking.
- **Separate "reserve key" transaction with an `IN_PROGRESS` state:** needs timeout/recovery logic
  for keys left behind by crashed requests. Doing everything in one transaction avoids that whole class of problems.
