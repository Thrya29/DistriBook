"""Helpers for classifying PostgreSQL errors raised through SQLAlchemy and asyncpg."""

from collections.abc import Iterator

# https://www.postgresql.org/docs/current/errcodes-appendix.html
UNIQUE_VIOLATION = "23505"
SERIALIZATION_FAILURE = "40001"
DEADLOCK_DETECTED = "40P01"
LOCK_NOT_AVAILABLE = "55P03"
QUERY_CANCELED = "57014"

RETRYABLE_SQLSTATES = frozenset({SERIALIZATION_FAILURE, DEADLOCK_DETECTED})


def _chain(exc: BaseException) -> Iterator[BaseException]:
    """Yield ``exc`` and everything it wraps (SQLAlchemy ``orig``, ``__cause__``)."""
    seen: set[int] = set()
    stack: list[BaseException | None] = [exc]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        stack.extend([getattr(current, "orig", None), current.__cause__])


def sqlstate(exc: BaseException) -> str | None:
    for err in _chain(exc):
        code = getattr(err, "sqlstate", None)
        if isinstance(code, str):
            return code
    return None


def constraint_name(exc: BaseException) -> str | None:
    for err in _chain(exc):
        name = getattr(err, "constraint_name", None)
        if isinstance(name, str):
            return name
    return None
