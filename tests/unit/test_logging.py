import json
import logging

from distribook.observability.logging import JsonFormatter, request_id_var


def make_record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        "distribook.test", logging.INFO, __file__, 1, "hello %s", ("x",), None
    )
    for name, value in extra.items():
        setattr(record, name, value)
    return record


def test_json_line_carries_request_id_and_fields() -> None:
    token = request_id_var.set("req-42")
    try:
        line = JsonFormatter().format(make_record(fields={"status": 201, "route": "/v1/x"}))
    finally:
        request_id_var.reset(token)

    payload = json.loads(line)
    assert payload["msg"] == "hello x"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "distribook.test"
    assert payload["request_id"] == "req-42"
    assert payload["status"] == 201
    assert payload["route"] == "/v1/x"
    assert payload["ts"].endswith("+00:00")


def test_json_line_without_request_context() -> None:
    payload = json.loads(JsonFormatter().format(make_record()))
    assert "request_id" not in payload
