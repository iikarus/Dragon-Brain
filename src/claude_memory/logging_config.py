"""Structured logging configuration for production observability.

Uses QueueHandler + QueueListener to move all I/O off the asyncio event
loop thread.  This prevents synchronous stderr/file writes from blocking
MCP request dispatch under burst load (see GOTCHAS #9).
"""

import atexit
import json
import logging
import logging.handlers
import os
import queue
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


class JSONFormatter(logging.Formatter):
    """Log formatter that outputs JSON lines for production log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record as a JSON string."""
        log_data: dict[str, Any] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            log_data["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "extra_data"):
            log_data["data"] = record.extra_data
        return json.dumps(log_data, default=str)


def configure_logging(level: str | None = None) -> None:
    """Configure logging for the application.

    Uses JSON format when LOG_FORMAT=json env var is set, otherwise human-readable.
    All handlers are wrapped in a QueueListener so log I/O never blocks the
    asyncio event loop thread.

    Args:
        level: Log level override. Defaults to LOG_LEVEL env var or INFO.
    """
    log_level = level or os.getenv("LOG_LEVEL") or "INFO"
    log_format = os.getenv("LOG_FORMAT", "text")

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplication
    root.handlers.clear()

    # --- Build target handlers (these do actual I/O) ---
    stderr_handler = logging.StreamHandler(sys.stderr)
    if log_format.lower() == "json":
        stderr_handler.setFormatter(JSONFormatter())
    else:
        stderr_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    log_dir = Path(os.getenv("DRAGON_BRAIN_LOG_DIR", str(Path.home() / ".claude" / "logs")))
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "dragon-brain.log",
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(JSONFormatter())
    file_handler.setLevel(logging.DEBUG)

    # --- QueueHandler + QueueListener: all I/O off the event loop thread ---
    log_queue: queue.Queue[logging.LogRecord] = queue.Queue(-1)
    queue_handler = logging.handlers.QueueHandler(log_queue)
    root.addHandler(queue_handler)

    listener = logging.handlers.QueueListener(
        log_queue, stderr_handler, file_handler, respect_handler_level=True
    )
    listener.start()
    atexit.register(listener.stop)
