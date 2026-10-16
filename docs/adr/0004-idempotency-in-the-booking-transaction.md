# ADR-0004: Idempotency keys are recorded inside the booking transaction

**Status:** Accepted · **Date:** 2026-10-16

## Context

Clients retry after timeouts and dropped connections. Without idempotency, a retry after a
successful-but-unacknowledged booking would either double-book or wrongly report a conflict. The
mechanism must also handle the *same* request arriving twice at the same moment.

## Decision

- `POST /v1/bookings` requires an `Idempotency-Key` header. Keys are scoped by `user_id` and kept for 24 h.
- In the same transaction as the booking: `INSERT INTO idempotency_keys … ON CONFLICT DO NOTHING`.
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

## Alternatives considered

- **Key stored in Redis:** a second system whose state could disagree with the booking.
- **Separate "reserve key" transaction with an `IN_PROGRESS` state:** needs timeout/recovery logic
  for keys left behind by crashed requests. Doing everything in one transaction avoids that whole class of problems.
