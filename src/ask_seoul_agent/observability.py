"""Minimal structured application logging with an explicit safe-field contract."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

_SAFE_EXTRA_FIELDS = (
    "trace_id",
    "provider",
    "status",
    "elapsed_ms",
    "tool_count",
    "tool_name",
    "tool_call_id",
    "evidence_status",
    "input_tokens",
    "output_tokens",
    "request_id",
    "upstream_status",
    "retry_count",
    "exception_type",
)
_HANDLER_NAME = "ask-seoul-json"


class JsonFormatter(logging.Formatter):
    """Serialize a bounded allowlist; arbitrary record extras are never emitted."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        for field in _SAFE_EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None and isinstance(value, (str, int, float, bool)):
                payload[field] = value
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    """Install one idempotent JSON handler for this package only."""

    normalized = level.strip().upper()
    numeric_level = logging.getLevelNamesMapping().get(normalized, logging.INFO)
    logger = logging.getLogger("ask_seoul_agent")
    logger.setLevel(numeric_level)
    logger.propagate = False

    managed = next(
        (handler for handler in logger.handlers if handler.get_name() == _HANDLER_NAME),
        None,
    )
    if managed is None:
        managed = logging.StreamHandler()
        managed.set_name(_HANDLER_NAME)
        logger.addHandler(managed)
    managed.setLevel(numeric_level)
    managed.setFormatter(JsonFormatter())
