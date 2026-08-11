import io
import json
import logging

from plimsoll_api.logging import configure_logging, request_id_var


def _capture(message: str) -> dict[str, str]:
    """Log one record through the configured root handler and return it parsed."""
    configure_logging("INFO")
    handler = logging.getLogger().handlers[0]
    buffer = io.StringIO()
    original = handler.stream  # type: ignore[attr-defined]
    handler.stream = buffer  # type: ignore[attr-defined]
    try:
        logging.getLogger("plimsoll.test").info(message)
    finally:
        handler.stream = original  # type: ignore[attr-defined]
    parsed: dict[str, str] = json.loads(buffer.getvalue().strip())
    return parsed


def test_log_records_are_json_carrying_the_request_id() -> None:
    token = request_id_var.set("req-abc")
    try:
        record = _capture("hello")
    finally:
        request_id_var.reset(token)

    assert record["message"] == "hello"
    assert record["requestId"] == "req-abc"
    assert record["level"] == "INFO"


def test_records_outside_a_request_carry_the_placeholder() -> None:
    assert _capture("no request here")["requestId"] == "-"


def test_the_logger_name_is_recorded() -> None:
    assert _capture("named")["logger"] == "plimsoll.test"
