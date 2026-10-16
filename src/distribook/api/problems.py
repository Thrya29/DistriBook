"""Maps every failure to an RFC 9457 ``application/problem+json`` response."""

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException

from distribook.db.errors import LOCK_NOT_AVAILABLE, QUERY_CANCELED, sqlstate
from distribook.domain.errors import DatabaseUnavailable, DomainError, ServiceOverloaded

PROBLEM_JSON = "application/problem+json"
logger = logging.getLogger(__name__)


def problem_response(err: DomainError) -> JSONResponse:
    headers = {"Retry-After": str(err.retry_after_s)} if err.retry_after_s else None
    return JSONResponse(err.problem(), err.status, headers=headers, media_type=PROBLEM_JSON)


def _plain_problem(status: int, code: str, title: str, **extra: Any) -> JSONResponse:
    body = {"type": "about:blank", "title": title, "status": status, "code": code, **extra}
    return JSONResponse(body, status, media_type=PROBLEM_JSON)


def classify_db_error(exc: DBAPIError) -> DomainError | None:
    """Translate infrastructure failures into retryable 503s; None means a genuine bug."""
    state = sqlstate(exc)
    if state in (QUERY_CANCELED, LOCK_NOT_AVAILABLE):
        return ServiceOverloaded()
    if exc.connection_invalidated or state is None or state.startswith("08"):
        return DatabaseUnavailable()
    return None


def register_problem_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _domain(_: Request, exc: DomainError) -> JSONResponse:
        return problem_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in exc.errors()]
        return _plain_problem(422, "VALIDATION_ERROR", "Request validation failed", errors=errors)

    @app.exception_handler(HTTPException)
    async def _http(_: Request, exc: HTTPException) -> JSONResponse:
        return _plain_problem(exc.status_code, f"HTTP_{exc.status_code}", str(exc.detail))

    @app.exception_handler(PoolTimeoutError)
    async def _pool_timeout(_: Request, exc: PoolTimeoutError) -> JSONResponse:
        return problem_response(ServiceOverloaded("No database connection available in time."))

    @app.exception_handler(DBAPIError)
    async def _db(_: Request, exc: DBAPIError) -> JSONResponse:
        mapped = classify_db_error(exc)
        if mapped is None:
            logger.error("unhandled database error", exc_info=exc)
            return _plain_problem(500, "INTERNAL_ERROR", "Internal error")
        logger.warning("database unavailable or overloaded: %s", type(exc).__name__)
        return problem_response(mapped)

    @app.exception_handler(OSError)
    async def _os(_: Request, exc: OSError) -> JSONResponse:
        # Connection refused / connect timeout while opening a new pooled connection.
        logger.warning("database connection failed: %s", exc)
        return problem_response(DatabaseUnavailable())

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled error", exc_info=exc)
        return _plain_problem(500, "INTERNAL_ERROR", "Internal error")
