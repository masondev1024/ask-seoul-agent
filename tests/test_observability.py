import json
import logging


def test_json_formatter_emits_only_allowlisted_operational_fields() -> None:
    from ask_seoul_agent.observability import JsonFormatter

    record = logging.LogRecord(
        name="ask_seoul_agent.agent",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="agent_completed",
        args=(),
        exc_info=None,
    )
    record.trace_id = "trace-1"
    record.provider = "demo"
    record.elapsed_ms = 42
    record.secret_value = "must-not-be-serialized"

    payload = json.loads(JsonFormatter().format(record))

    assert payload["event"] == "agent_completed"
    assert payload["level"] == "INFO"
    assert payload["trace_id"] == "trace-1"
    assert payload["provider"] == "demo"
    assert payload["elapsed_ms"] == 42
    assert "secret_value" not in payload
    assert "must-not-be-serialized" not in str(payload)


def test_configure_logging_is_idempotent() -> None:
    from ask_seoul_agent.observability import configure_logging

    logger = logging.getLogger("ask_seoul_agent")
    original_handlers = list(logger.handlers)
    original_level = logger.level
    original_propagate = logger.propagate
    try:
        logger.handlers.clear()
        configure_logging("INFO")
        configure_logging("DEBUG")

        managed = [
            handler for handler in logger.handlers if handler.get_name() == "ask-seoul-json"
        ]
        assert len(managed) == 1
        assert logger.level == logging.DEBUG
        assert logger.propagate is False
    finally:
        logger.handlers[:] = original_handlers
        logger.setLevel(original_level)
        logger.propagate = original_propagate
