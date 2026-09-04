"""Structured JSON logging with a per-request correlation id.

THE CARD NUMBER CANNOT REACH THIS MODULE. That is enforced upstream, not here:

  1. The field is SecretStr, so repr()/str() render a mask.
  2. Field(exclude=True) keeps it out of model_dump and model_dump_json.
  3. ConfigDict(hide_input_in_errors=True) keeps it out of ValidationError,
     which is what the 422 handler renders.
  4. get_secret_value() is called in exactly two places, neither of which logs.

There is deliberately NO regex scrubber here. A scrubber would imply the number
can reach the logger and be caught on the way out; the design is that it never
arrives. It would also invite false confidence and mangle legitimate long digit
strings such as order ids.
"""

import json
import logging
from contextvars import ContextVar
from typing import Any

correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the request's correlation id attached."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": correlation_id.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
