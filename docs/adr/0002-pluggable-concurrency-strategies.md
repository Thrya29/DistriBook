# ADR-0002: Pluggable concurrency strategies under READ COMMITTED

**Status:** Accepted · **Date:** 2026-10-16

## Context

There are several well-known ways to make "check availability, then claim" safe. They behave very
differently under contention, and the right choice depends on the workload. Just asserting that one
is best is weaker than measuring them against each other.

## Decision

- Concurrency control sits behind a `BookingStrategy` interface, chosen per deployment by configuration.
- Strategies: `conditional` (default), `pessimistic`, `pessimistic_nowait`, `redis_lock`, and
  `naive`, which is deliberately unsafe, test-only, and refused in production.
- Transactions use PostgreSQL's default **READ COMMITTED** isolation level. Correctness comes from
  row locks and conditional writes, whose re-check after waiting for a lock is guaranteed by
  PostgreSQL's update semantics.
- All multi-row locking happens in ascending `seats.id` order to rule out deadlocks.
- `conditional` is the default because it combines the check and the write in a single statement,
  with the fewest round trips while locks are held.

## Consequences

- The same correctness and load suites run against every strategy. The comparison is like-for-like.
- `naive` lets us show that the tests *can* detect a race. Without that, a passing test would prove nothing.
- More code paths to maintain. This is limited by keeping each strategy to just its `claim()` step.

## Alternatives considered

- **SERIALIZABLE isolation:** correct, but losers get serialisation failures that have to be retried,
  which adds latency and makes outcomes less predictable under hot contention. It would also let
  tests pass without showing *which* mechanism is doing the work.
- **Optimistic version check (`WHERE version = :read_version`):** for a two-state status, the
  condition `status = 'AVAILABLE'` already is the compare-and-set, so a version check adds a
  round trip and nothing more. `version` is kept for observability.
- **Advisory locks:** they work, but they live outside the data they protect and are easier to misuse.
