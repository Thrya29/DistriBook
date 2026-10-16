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
| 1 | [Requirements](docs/requirements.md), [system design](docs/system-design.md), [ADRs](docs/adr/) | ✅ Done |
| 2 | Schema & migrations, full booking API (`conditional` strategy), idempotency, cancellation, integration tests | ✅ Done |
| 3 | Concurrency & failure test suites, plus proof that the suite catches a race | ✅ Done |
| 4 | `pessimistic`, `pessimistic_nowait`, `redis_lock` strategies; structured logs & Prometheus metrics | ⏳ Next |
| 5 | Multi-replica Docker Compose stack (nginx), k6 scenarios, benchmark pipeline | ⏳ |
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
5. **Proof the tests can catch a race:** the contention suite is also run against a deliberately
   unsafe strategy with the safety-net index removed, and it detects the double booking.

The full reasoning is in the [system design](docs/system-design.md) and [ADRs](docs/adr/).

## API

| Method & path | Purpose |
|---|---|
| `POST /v1/events` | Create an event with a generated seat grid |
| `GET /v1/events/{id}` | Event with seat counts |
| `GET /v1/events/{id}/seats?status=&limit=&after_id=` | List seats (keyset pagination) |
| `GET /v1/seats/{id}` | One seat |
| `POST /v1/bookings` + `Idempotency-Key` header | Book 1–10 seats, all or nothing |
| `GET /v1/bookings/{id}` | One booking |
| `POST /v1/bookings/{id}/cancel` | Cancel (idempotent) |
| `GET /healthz`, `GET /readyz` | Liveness / readiness |

Errors are [RFC 9457](https://www.rfc-editor.org/rfc/rfc9457) `application/problem+json` with a
stable `code` (`SEAT_UNAVAILABLE`, `IDEMPOTENCY_KEY_REUSED`, `SERVICE_OVERLOADED`, …). Interactive
docs are served at `/docs` while the app is running.

```console
$ curl -X POST localhost:8000/v1/bookings -H 'Idempotency-Key: 7f3c…' \
       -H 'content-type: application/json' \
       -d '{"event_id": 1, "user_id": "alice", "seat_ids": [1, 2]}'
HTTP/1.1 201 Created
Location: /v1/bookings/fe11177f-2fa8-4f85-8426-abc76b13a54a

# a different client asking for the same seats
HTTP/1.1 409 Conflict
Content-Type: application/problem+json
{"code": "SEAT_UNAVAILABLE", "unavailable_seat_ids": [1, 2], ...}
```

## What the tests prove

Every concurrency and integration test ends by running SQL checks against the database for the
[correctness invariants](docs/requirements.md#6-correctness-invariants), e.g. *no seat has two
active bookings*. The contention tests run against **two API replicas with separate connection
pools** sharing one database, with every request released at the same instant.

| Scenario | Result asserted |
|---|---|
| 500 requests for one seat | exactly 1 × `201`, 499 × `409 SEAT_UNAVAILABLE`, one claim in the database |
| 200 requests for 200 different seats | all `201` |
| 300 overlapping 2–4-seat requests, seats in random order | only `201`/`409`, winners never share a seat, losers leave no trace, no deadlocks |
| 50 identical retries with one idempotency key | all `201`, one booking |
| 30 different bodies with one idempotency key | 1 × `201`, 29 × `422 IDEMPOTENCY_KEY_REUSED` |
| Cancellation racing 30 new bookings (15 rounds) | the seat goes to at most one new owner |
| Unsafe `naive` strategy, safety-net index present | the database still allows exactly one winner |
| Unsafe `naive` strategy, safety-net index removed | **the suite detects the double booking** |
| Session killed between claim and commit | `503`; no booking, seat free, key unused; retry with same key → `201` |
| Every pooled connection killed (database restart) | the next requests succeed |
| Database unreachable / pool exhausted | `503` + `Retry-After` within bounded time |
| Seat row locked elsewhere / duplicate of a stuck request | `409 SEAT_CONTENDED` / `409 REQUEST_IN_PROGRESS`; not stored, so the retry succeeds |

The suite also caught a real bug during development: a lock convoy in the idempotency step,
written up in [ADR-0004](docs/adr/0004-idempotency-in-the-booking-transaction.md#lesson-from-testing-do-nothing-not-do-update--where).

## Running locally

Requires Python 3.12+, [uv](https://docs.astral.sh/uv/) and Docker.

```bash
docker compose up -d --wait postgres     # PostgreSQL 16 on 127.0.0.1:5432
uv sync                                  # create .venv with locked dependencies
uv run alembic upgrade head              # apply migrations
uv run uvicorn distribook.main:create_app --factory --port 8000
```

Settings are read from `DISTRIBOOK_*` environment variables. See [.env.example](.env.example).
A single `docker compose up` for the whole multi-replica stack arrives in Phase 5.

## Development

```bash
uv run pytest              # all tests (needs the postgres container running)
uv run ruff check .        # lint
uv run ruff format .       # format
uv run mypy                # type-check (strict)
```

Integration tests run against a real PostgreSQL. The test database (`distribook_test`) is dropped,
recreated and migrated at the start of each run, and the suite refuses to run against any
database whose name doesn't end in `_test`. CI runs the same tests against a PostgreSQL service
container, and checks that migrations can be applied and fully reversed.

## Repository layout

```
src/distribook/
├── api/             HTTP routes, problem+json error mapping
├── domain/          request/response models, domain errors
├── services/        booking & catalog orchestration, idempotency, retry policy
├── strategies/      pluggable concurrency control (conditional; naive = unsafe, test-only)
├── db/              connection pool, PostgreSQL error classification
├── invariants.py    SQL checks for the correctness invariants
└── main.py          application factory
migrations/          Alembic migrations (explicit DDL)
tests/unit/          pure logic
tests/integration/   every endpoint against real PostgreSQL
tests/concurrency/   simultaneous requests across two replicas; race-detector proof
tests/failure/       killed sessions, outages, exhausted pool, held locks
docs/                requirements, system design, ADRs
```

## License

[MIT](LICENSE)
