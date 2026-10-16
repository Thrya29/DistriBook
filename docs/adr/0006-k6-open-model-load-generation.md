# ADR-0006: k6 with an open (arrival-rate) workload model

**Status:** Accepted · **Date:** 2026-10-16

## Context

Published latency percentiles are only meaningful if the load generator (a) is not itself the
bottleneck, and (b) doesn't stop sending requests while the server is slow. In a *closed* model,
each virtual user waits for a response before sending the next request. When the server slows down,
fewer requests are sent, so the requests that would have suffered the most delay never happen and are
never measured. This is called *coordinated omission*, and it makes tail latency look better than it is.

## Decision

- Use **k6**, run from its official Docker image so the version is pinned and no local install is needed.
- Use the `constant-arrival-rate` and `ramping-arrival-rate` executors (an **open model**): new
  requests start on schedule whether or not earlier ones have finished. When k6 can't keep up, it
  reports dropped iterations, and any run with dropped iterations is marked invalid.
- Export raw per-run results as JSON. Percentiles and charts are computed by a committed script,
  so every published number can be regenerated.

## Consequences

- Tail latency includes queueing delay, so the numbers are honest and look worse than closed-model numbers would.
- The machine generating load shares the host with the system under test. This is recorded as a
  limitation, and k6's CPU use is monitored.

## Alternatives considered

- **Locust:** Python's GIL limits how much load one process can generate, and its default model is
  closed. It is fine for functional load but weaker for tail-latency measurement.
- **wrk2:** uses a correct open model, but scripting the request mix and validating responses is harder.
- **Custom asyncio client:** used only in the pytest concurrency suite, where an exact simultaneous
  start (`asyncio.Barrier`) matters more than the arrival rate.
