# DistriBook

[![CI](https://github.com/Thrya29/DistriBook/actions/workflows/ci.yml/badge.svg)](https://github.com/Thrya29/DistriBook/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**A seat-booking service that never double-books, with reproducible load tests and published latency percentiles.**

When hundreds of clients try to book the same seat at the same moment, exactly one must win and
everyone else must get a clear, correct answer, even with several API replicas, client retries and
crashed processes. DistriBook implements this with PostgreSQL as the single source of truth,
compares **several concurrency-control strategies** under identical workloads, and publishes how
each one behaves under load.

## Project status

> 🚧 **In active development.** This section is updated with every push.

| Phase | Scope | Status |
|---|---|---|
| 0 | Repository skeleton, tooling, CI | ✅ Done |
| 1 | [Requirements](docs/requirements.md), [system design](docs/system-design.md), [ADRs](docs/adr/) | 📝 Drafted, under review |
| 2 | Schema, migrations, core API (`conditional` strategy) | ⏳ Next |
| 3 | Concurrency & failure test suites, idempotency, multi-seat bookings | ⏳ |
| 4 | `pessimistic`, `pessimistic_nowait`, `redis_lock` strategies; observability | ⏳ |
| 5 | Multi-replica Docker Compose stack, k6 scenarios, benchmark pipeline | ⏳ |
| 6 | Benchmark runs, analysis, final documentation | ⏳ |

## How double booking is prevented (summary)

1. **One atomic statement** checks and claims the seats:
   `UPDATE seats … WHERE status = 'AVAILABLE'`. Under contention, only one transaction can change
   the row. The others re-check the condition after the winner commits and get `409 Conflict`.
2. **A database safety net:** a partial unique index allows at most one active claim per seat, so
   even buggy application code cannot double-book.
3. **Deadlock-free multi-seat bookings:** every transaction locks seats in ascending ID order.
4. **Safe retries:** an `Idempotency-Key` is recorded in the *same transaction* as the booking, so a
   retried request replays the original result instead of booking twice.
5. **Proof the tests can catch a race:** the concurrency suite is also run against a deliberately
   unsafe strategy with the safety net removed, and must detect the double booking.

The full reasoning is in the [system design](docs/system-design.md) and [ADRs](docs/adr/).

## Planned stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 Core + asyncpg · PostgreSQL 16 · Alembic · Redis 7 (one
strategy only) · pytest · k6 · nginx · Prometheus/Grafana · Docker Compose · GitHub Actions

## Development

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                    # create .venv with locked dependencies
uv run ruff check .        # lint
uv run ruff format .       # format
uv run mypy                # type-check (strict)
uv run pytest              # tests
```

`docker compose up` to run the whole system arrives in Phase 5.

## Repository layout

```
docs/                  requirements, system design, architecture decision records
src/distribook/        application package
tests/                 automated tests
.github/workflows/     CI
```

## License

[MIT](LICENSE)
