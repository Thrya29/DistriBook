# ADR-0005: SQLAlchemy Core (not ORM); one uvicorn worker per container

**Status:** Accepted · **Date:** 2026-10-16

## Context

The project's correctness rests on exactly which SQL statements run, in what order, with which
locks. The deployment must also scale horizontally and report clean metrics.

## Decision

- Use **SQLAlchemy 2.0 Core** with the **asyncpg** driver. Queries are written explicitly. No ORM
  unit-of-work, lazy loading or automatic flushing.
- Migrations are managed by **Alembic**.
- Each container runs **one uvicorn worker**. To get more capacity, add replicas.

## Consequences

- Every lock and every round trip is visible in the code and can be reviewed against the design.
- One process means one connection pool and one Prometheus registry per container: no
  multiprocess-metrics workarounds, and easy-to-reason-about connection budgets (`replicas × pool_size`).
- A single worker uses one CPU core per container. That's acceptable because the workload is
  I/O-bound and async, and replicas provide the parallelism.

## Alternatives considered

- **SQLAlchemy ORM:** convenient, but it hides when statements are sent and which locks they take, the very things this project needs to show.
- **Raw asyncpg only:** the fastest option, but without a schema definition or migration integration.
- **Gunicorn with several workers per container:** complicates metrics and pool sizing for no benefit over replicas.
