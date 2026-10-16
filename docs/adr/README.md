# Architecture Decision Records

Short records of significant decisions, the context they were made in, and the alternatives considered.
Format: [Michael Nygard's ADR template](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).

| ADR | Decision | Status |
|---|---|---|
| [0001](0001-postgres-is-the-source-of-truth.md) | PostgreSQL is the single source of truth; constraints are the last line of defence | Accepted |
| [0002](0002-pluggable-concurrency-strategies.md) | Pluggable concurrency strategies under READ COMMITTED; conditional update is the default | Accepted |
| [0003](0003-redis-is-an-optimisation-not-a-correctness-dependency.md) | Redis is an optimisation, never a correctness dependency | Accepted |
| [0004](0004-idempotency-in-the-booking-transaction.md) | Idempotency keys are recorded inside the booking transaction | Accepted |
| [0005](0005-sqlalchemy-core-and-single-worker-containers.md) | SQLAlchemy Core (not ORM); one uvicorn worker per container | Accepted |
| [0006](0006-k6-open-model-load-generation.md) | k6 with an open (arrival-rate) workload model | Accepted |
