"""Runtime configuration. Every setting is read from a ``DISTRIBOOK_*`` environment variable."""

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

StrategyName = Literal["conditional", "pessimistic", "pessimistic_nowait", "redis_lock", "naive"]
UNSAFE_STRATEGIES: frozenset[str] = frozenset({"naive"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DISTRIBOOK_", frozen=True)

    env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_json: bool = True

    database_url: str = "postgresql+asyncpg://distribook:distribook@127.0.0.1:5432/distribook"
    db_pool_size: int = Field(default=10, ge=1, le=100)
    db_pool_timeout_s: float = Field(default=2.0, gt=0)
    db_connect_timeout_s: float = Field(default=5.0, gt=0)
    db_statement_timeout_ms: int = Field(default=2000, ge=1)
    db_lock_timeout_ms: int = Field(default=1000, ge=1)
    db_idle_in_transaction_timeout_ms: int = Field(default=5000, ge=1)
    db_pool_prewarm: bool = True

    booking_strategy: StrategyName = "conditional"
    enable_unsafe_strategies: bool = False

    # Only used by the redis_lock strategy. Redis is an optimisation, never a correctness
    # dependency (ADR-0003), so its timeouts are short: a slow Redis is skipped, not waited on.
    redis_url: str = "redis://127.0.0.1:6380/0"
    redis_lock_ttl_ms: int = Field(default=2000, ge=1)
    redis_timeout_s: float = Field(default=0.25, gt=0)
    redis_skip_after_failure_s: float = Field(default=5.0, ge=0)

    idempotency_ttl_s: int = Field(default=24 * 60 * 60, ge=60)
    idempotency_purge_interval_s: float = Field(default=300.0, ge=0, description="0 disables")
    idempotency_purge_batch_size: int = Field(default=1000, ge=1)
    txn_max_attempts: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def _lock_wait_must_be_shorter_than_statement(self) -> "Settings":
        # A lock wait that outlives the statement would surface as a generic timeout
        # instead of the more precise "contended" error.
        if self.db_lock_timeout_ms >= self.db_statement_timeout_ms:
            raise ValueError("db_lock_timeout_ms must be smaller than db_statement_timeout_ms")
        return self

    @model_validator(mode="after")
    def _unsafe_strategies_need_explicit_opt_in(self) -> "Settings":
        if self.booking_strategy in UNSAFE_STRATEGIES:
            if self.env == "production":
                raise ValueError(
                    f"strategy {self.booking_strategy!r} is never allowed in production"
                )
            if not self.enable_unsafe_strategies:
                raise ValueError(
                    f"strategy {self.booking_strategy!r} can double-book; "
                    "set enable_unsafe_strategies=true to use it for testing"
                )
        return self

    @property
    def server_settings(self) -> dict[str, str]:
        """PostgreSQL session settings applied to every pooled connection."""
        return {
            "application_name": "distribook",
            "statement_timeout": f"{self.db_statement_timeout_ms}ms",
            "lock_timeout": f"{self.db_lock_timeout_ms}ms",
            "idle_in_transaction_session_timeout": f"{self.db_idle_in_transaction_timeout_ms}ms",
        }
