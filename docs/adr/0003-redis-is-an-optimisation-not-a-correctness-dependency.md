# ADR-0003: Redis is an optimisation, never a correctness dependency

**Status:** Accepted · **Date:** 2026-10-16

## Context

Distributed locks in Redis (`SET NX PX`) are often presented as the way to stop double booking. But a
lock with a TTL can expire while its holder is still working (GC pause, slow query, network delay),
and Redis replication is asynchronous, so a failover can lose a lock that was already granted.
Either way, two clients can believe they hold the lock at the same time.

## Decision

- The `redis_lock` strategy takes per-seat Redis locks **before** using a database connection,
  so that under heavy contention the losers are turned away cheaply.
- The database step that follows is the same conditional write as the `conditional` strategy. The
  conditional `UPDATE … WHERE status = 'AVAILABLE'` is what fences out a holder whose lock has
  expired, so no separate fencing-token column is needed.
- If Redis is unavailable, the strategy falls back to `conditional` and increments a metric. It does
  not refuse requests.

## Consequences

- Losing Redis, or a lock expiring, can never cause a double booking. At worst it causes extra load
  on the database.
- Redis adds a network round trip to every booking, so it may be *slower* on uncontended workloads.
  The benchmarks measure this; it is not assumed either way.

## Alternatives considered

- **Redlock across several Redis nodes:** reduces some failure cases but still relies on timing
  assumptions, and it adds operational complexity for no correctness gain here, since the database
  is already the authority.
- **Failing closed when Redis is down:** would turn an optional optimisation into a single point of failure.
