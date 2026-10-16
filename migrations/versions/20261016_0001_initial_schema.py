"""Initial schema: events, seats, bookings, claim history, idempotency keys.

Mirrors docs/system-design.md section 3. Written as explicit DDL so the constraints that carry
the correctness guarantees are reviewable exactly as PostgreSQL will see them.

Revision ID: 0001
Revises:
Create Date: 2026-10-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

UPGRADE = [
    "CREATE TYPE seat_status AS ENUM ('AVAILABLE', 'BOOKED')",
    "CREATE TYPE booking_status AS ENUM ('CONFIRMED', 'CANCELLED')",
    """
    CREATE TABLE events (
        id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        name        text        NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
        starts_at   timestamptz NOT NULL,
        created_at  timestamptz NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE bookings (
        id            uuid PRIMARY KEY,
        event_id      bigint         NOT NULL REFERENCES events (id),
        user_id       text           NOT NULL CHECK (length(user_id) BETWEEN 1 AND 128),
        status        booking_status NOT NULL DEFAULT 'CONFIRMED',
        created_at    timestamptz    NOT NULL DEFAULT now(),
        cancelled_at  timestamptz,
        UNIQUE (id, event_id),
        CHECK ((status = 'CANCELLED') = (cancelled_at IS NOT NULL))
    )
    """,
    "CREATE INDEX bookings_event_idx ON bookings (event_id)",
    """
    CREATE TABLE seats (
        id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        event_id    bigint      NOT NULL REFERENCES events (id),
        label       text        NOT NULL,
        status      seat_status NOT NULL DEFAULT 'AVAILABLE',
        booking_id  uuid        REFERENCES bookings (id),
        version     integer     NOT NULL DEFAULT 0,
        updated_at  timestamptz NOT NULL DEFAULT now(),
        UNIQUE (event_id, label),
        UNIQUE (event_id, id),
        CHECK ((status = 'BOOKED') = (booking_id IS NOT NULL))
    )
    """,
    "CREATE INDEX seats_event_status_idx ON seats (event_id, status, id)",
    "CREATE INDEX seats_booking_idx ON seats (booking_id) WHERE booking_id IS NOT NULL",
    """
    CREATE TABLE booking_seats (
        booking_id   uuid        NOT NULL,
        event_id     bigint      NOT NULL,
        seat_id      bigint      NOT NULL,
        claimed_at   timestamptz NOT NULL DEFAULT now(),
        released_at  timestamptz,
        PRIMARY KEY (booking_id, seat_id),
        FOREIGN KEY (booking_id, event_id) REFERENCES bookings (id, event_id),
        FOREIGN KEY (event_id, seat_id)    REFERENCES seats (event_id, id)
    )
    """,
    # THE SAFETY NET (INV-1): at most one unreleased claim per seat.
    """
    CREATE UNIQUE INDEX booking_seats_one_active_claim
        ON booking_seats (seat_id) WHERE released_at IS NULL
    """,
    """
    CREATE TABLE idempotency_keys (
        user_id        text        NOT NULL,
        key            text        NOT NULL CHECK (length(key) BETWEEN 1 AND 255),
        request_hash   bytea       NOT NULL,
        response_code  smallint,
        response_body  jsonb,
        booking_id     uuid        REFERENCES bookings (id),
        created_at     timestamptz NOT NULL DEFAULT now(),
        expires_at     timestamptz NOT NULL,
        PRIMARY KEY (user_id, key)
    )
    """,
    "CREATE INDEX idempotency_keys_expiry_idx ON idempotency_keys (expires_at)",
]

DOWNGRADE = [
    "DROP TABLE idempotency_keys",
    "DROP TABLE booking_seats",
    "DROP TABLE seats",
    "DROP TABLE bookings",
    "DROP TABLE events",
    "DROP TYPE booking_status",
    "DROP TYPE seat_status",
]


def upgrade() -> None:
    # One statement per call: asyncpg executes prepared statements, which cannot hold several.
    for statement in UPGRADE:
        op.execute(statement)


def downgrade() -> None:
    for statement in DOWNGRADE:
        op.execute(statement)
