# DistriBook — Requirements

| | |
|---|---|
| **Status** | Draft for review |
| **Last updated** | 2026-10-16 |
| **Related** | [System design](system-design.md) · [ADRs](adr/) |

## 1. Problem statement

When many clients try to reserve the same limited resource at the same moment, a booking system has
to pick **exactly one** winner per seat, tell everyone else clearly that they lost, and stay
consistent through retries, crashes and multiple application instances. Most booking bugs
(double-sold seats, phantom confirmations, orphaned reservations) come from getting this wrong.

DistriBook is a seat-booking service whose main goal is **correctness under contention**, with
**measured, reproducible** performance.

## 2. Goals

1. Never confirm the same seat to more than one active booking, whatever the concurrency level,
   number of API replicas, client retries or process crashes.
2. Give every client a clear, correct result: booked, conflict, invalid, or "try again later".
3. Prove (1) and (2) with automated tests that are shown to fail against a deliberately unsafe implementation.
4. Compare several concurrency-control strategies under the same workloads and publish latency
   percentiles, throughput and error rates, with everything needed to reproduce them.
5. Be deployable with a single `docker compose up`.

## 3. Non-goals

These are deliberately out of scope. Each would be needed for a real product, but none affects the
concurrency problem this project is about.

- Authentication and authorization. `user_id` is treated as a trusted value, as if an upstream
  identity gateway had already authenticated it.
- Payments, pricing, refunds.
- Seat maps, front-end UI.
- Multi-region deployment and cross-region replication.
- Temporary holds with expiry (hold → pay → confirm). This is a **stretch goal**, described in the system design.

## 4. Glossary

| Term | Meaning |
|---|---|
| Event | Something with a fixed set of seats (a concert, a screening). |
| Seat | One bookable unit belonging to an event. Has a human label such as `C14`. |
| Booking | One client's successful claim over one or more seats of a single event. |
| Active booking | A booking whose status is `CONFIRMED`. A cancelled booking is not active. |
| Strategy | One interchangeable implementation of the booking concurrency control. |
| Idempotency key | A client-generated unique string that makes a retried request safe. |

## 5. Functional requirements

| ID | Requirement |
|---|---|
| FR-1 | Create an event with a generated seat grid (rows × seats per row). |
| FR-2 | Get an event and list its seats, filtered by status, with pagination. |
| FR-3 | Get a single seat's current state. |
| FR-4 | Book **1–10 seats** of one event in one request. All the seats are booked, or none are. |
| FR-5 | If any requested seat is unavailable, reject the whole request and name the unavailable seats. |
| FR-6 | Every booking request carries an `Idempotency-Key`. Retrying with the same key and same body returns the original result without booking again. |
| FR-7 | Reusing an idempotency key with a *different* body is rejected. |
| FR-8 | Get a booking by ID. |
| FR-9 | Cancel a booking, which frees its seats immediately. Cancelling an already-cancelled booking succeeds and changes nothing. |
| FR-10 | Expose liveness, readiness and Prometheus metrics endpoints. |

## 6. Correctness invariants

These are the properties the system must **never** violate. Each one is checked by automated tests
**and** by a SQL check that runs after every load test (see the system design, §9).

| ID | Invariant |
|---|---|
| **INV-1** | A seat belongs to at most one active booking. |
| **INV-2** | A booking's seats were all claimed in one transaction. No booking is ever partially applied. |
| **INV-3** | `seats.status = 'BOOKED'` if and only if the seat has exactly one active booking, and `seats.booking_id` points to it. |
| **INV-4** | A given `(user_id, idempotency_key)` produces at most one booking. |
| **INV-5** | Every booking's seats belong to the booking's event. |
| **INV-6** | A request that returned `201 Created` corresponds to a durable, committed booking. |

## 7. Non-functional requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-1 | Correctness | Zero invariant violations across all test suites and all benchmark runs. |
| NFR-2 | Robustness | Under hot-seat contention (≥ 500 simultaneous requests for one seat): exactly one `201`. Every other response is `409`, or a `503` + `Retry-After` for a request that was deliberately turned away because connection capacity ran out (NFR-3). Zero `500`s. When capacity is large enough for the burst, every loser gets `409`. |
| NFR-3 | Graceful degradation | When load is too high, the service sheds it with `503` + `Retry-After` within a bounded time (≤ 2 s) instead of letting requests wait indefinitely or time out silently. |
| NFR-4 | Horizontal scaling | Correctness holds with ≥ 2 stateless API replicas behind a load balancer. |
| NFR-5 | Failure safety | If the process or the database connection dies mid-request, no partial booking remains. A request is never reported as successful unless it was committed. |
| NFR-6 | Observability | Structured JSON logs with a request ID. Latency histograms and booking-outcome counters exported for Prometheus. |
| NFR-7 | Reproducibility | Each benchmark records its environment, versions, configuration and commit SHA, and keeps its raw output. One command regenerates every published chart and table. |
| NFR-8 | Operability | One-command start-up. Migrations apply automatically. Health checks gate traffic. Graceful shutdown lets in-flight requests finish. |
| NFR-9 | Code quality | Type-checked (`mypy --strict`) and linted. CI runs on every push. |

### Performance targets

No latency or throughput **targets** are set before a baseline has been measured. Inventing numbers
in advance would be meaningless on unknown hardware. The benchmark report will:

1. measure a baseline for each strategy and workload,
2. find the saturation point (where latency climbs sharply),
3. name the resource that limits throughput.

## 8. Acceptance criteria

The project is complete when:

- [ ] Every FR is implemented and covered by an integration test.
- [ ] The contention test passes for every safe strategy, **and** fails against the deliberately unsafe
      `naive` strategy when the database safety constraint is removed. This proves the test can detect a race.
- [ ] Failure tests pass: database restart, connection loss mid-transaction, concurrent duplicate idempotency keys.
- [ ] Benchmarks are published for every strategy × workload, with p50/p95/p99/p99.9, throughput,
      error rate, and a post-run invariant check showing zero violations.
- [ ] A fresh clone can go from `docker compose up` to reproducing the benchmarks by following the README.
