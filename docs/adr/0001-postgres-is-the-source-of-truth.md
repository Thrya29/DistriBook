# ADR-0001: PostgreSQL is the single source of truth

**Status:** Accepted · **Date:** 2026-10-16

## Context

The central requirement is that a seat is never confirmed to two active bookings (INV-1), even
with several stateless API replicas, retries and crashes. Whatever decides a booking's outcome must
be durable, transactional, and shared by every replica.

## Decision

- PostgreSQL holds all durable state. Every booking decision is made inside a PostgreSQL transaction.
- Invariants are enforced **in the schema** wherever they can be expressed there:
  a partial unique index allows one active claim per seat, composite foreign keys keep a booking's
  seats in its own event, and `CHECK` constraints keep each row consistent with itself.
- The application layer is responsible for doing things efficiently and returning clear errors.
  The schema is responsible for refusing anything incorrect.

## Consequences

- A bug in application code becomes a constraint violation (mapped to `409`/`500`), never a silent
  double booking.
- Throughput on a single hot seat is limited by serialised row-lock hand-off. That is inherent to
  having a single authority and is addressed by admission control, not by weakening correctness
  (see system design §13).
- Every replica needs a database connection, so the connection budget must be planned (§5).

## Alternatives considered

- **In-memory or per-process locks:** wrong as soon as there are two replicas.
- **Redis as the authority:** asynchronous replication and lock expiry mean a failover or a long
  GC pause can let two holders proceed. See ADR-0003.
- **Event sourcing / a single-writer queue per event:** a valid design at very large scale, but
  much more complex than needed to show correctness here. Noted as a future option.
