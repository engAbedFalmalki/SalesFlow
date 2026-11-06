"""Structured logging (JSON lines by default)."""

from __future__ import annotations

import atexit
import json
import logging
import logging.handlers
import queue
import sys
from datetime import datetime, timezone

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k not in _RESERVED and not k.startswith("_"):
                data[k] = v
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, default=str)


_listener: logging.handlers.QueueListener | None = None


def configure_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Log through a queue: callers (request threads, the event loop, the worker)
    only enqueue records, and one background thread writes them. A slow or full
    output pipe can then never stall request handling."""
    global _listener
    stream = logging.StreamHandler(sys.stderr)  # stdout stays clean for CLI JSON output
    if fmt == "json":
        stream.setFormatter(JsonFormatter())
    else:
        stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    if _listener is not None:
        _listener.stop()
    q: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
    _listener = logging.handlers.QueueListener(q, stream, respect_handler_level=True)
    _listener.start()
    root = logging.getLogger()
    root.handlers[:] = [logging.handlers.QueueHandler(q)]
    root.setLevel(level.upper())
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    # The API logs one structured line per request itself; uvicorn's access line would duplicate it.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


@atexit.register
def _flush_logs() -> None:
    if _listener is not None:
        _listener.stop()
