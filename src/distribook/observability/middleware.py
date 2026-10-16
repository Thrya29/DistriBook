"""Per-request context: request id, latency histogram, one access-log line.

A pure ASGI middleware rather than Starlette's BaseHTTPMiddleware, which adds a task and
buffering to every request; this sits on the hot path of the load tests.
"""

import logging
import re
import time
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from distribook.observability.logging import request_id_var
from distribook.observability.metrics import Metrics

REQUEST_ID_HEADER = "x-request-id"
_VALID_REQUEST_ID = re.compile(r"[\x21-\x7e]{1,128}")

access_log = logging.getLogger("distribook.access")


def _incoming_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", ()):
        if name == REQUEST_ID_HEADER.encode():
            candidate = value.decode("latin-1")
            return candidate if _VALID_REQUEST_ID.fullmatch(candidate) else None
    return None


def _route_template(scope: Scope) -> str:
    # The template ("/v1/bookings/{booking_id}"), never the raw path: raw paths would give the
    # histogram one time series per booking id.
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp, metrics: Metrics) -> None:
        self._app = app
        self._metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = _incoming_request_id(scope) or uuid4().hex
        token = request_id_var.set(request_id)
        status = 500  # what the client gets if the app raises before responding
        started = time.perf_counter()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message).append("X-Request-ID", request_id)
            await send(message)

        try:
            await self._app(scope, receive, send_with_request_id)
        finally:
            elapsed = time.perf_counter() - started
            route = _route_template(scope)
            self._metrics.http_request_duration.labels(route, scope["method"], str(status)).observe(
                elapsed
            )
            access_log.info(
                "request",
                extra={
                    "fields": {
                        "method": scope["method"],
                        "route": route,
                        "path": scope["path"],
                        "status": status,
                        "duration_ms": round(elapsed * 1000, 2),
                    }
                },
            )
            request_id_var.reset(token)
