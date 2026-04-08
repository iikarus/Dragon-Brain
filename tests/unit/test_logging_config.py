"""Tests for structured logging configuration."""

import json
import logging
import logging.handlers
import os
from unittest.mock import patch

from claude_memory.logging_config import JSONFormatter, configure_logging


class TestJSONFormatter:
    """Test the JSON log formatter."""

    def test_happy_formats_basic_log_record(self) -> None:
        """Basic log record is formatted as JSON."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test.logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello %s",
            args=("world",),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["logger"] == "test.logger"
        assert data["message"] == "hello world"
        assert "timestamp" in data

    def test_happy_formats_exception_info(self) -> None:
        """Exception info is included in the JSON output."""
        formatter = JSONFormatter()
        try:
            raise ValueError("test error")
        except ValueError:
            import sys

            record = logging.LogRecord(
                name="test",
                level=logging.ERROR,
                pathname="test.py",
                lineno=1,
                msg="oops",
                args=(),
                exc_info=sys.exc_info(),
            )
            output = formatter.format(record)
            data = json.loads(output)
            assert "exception" in data
            assert "ValueError" in data["exception"]

    def test_happy_includes_extra_data(self) -> None:
        """Extra data attribute is included if present."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="msg",
            args=(),
            exc_info=None,
        )
        record.extra_data = {"key": "value"}  # type: ignore[attr-defined]
        output = formatter.format(record)
        data = json.loads(output)
        assert data["data"] == {"key": "value"}


class TestConfigureLogging:
    """Test the configure_logging function."""

    def test_sad1_default_text_format(self) -> None:
        """Default configuration uses QueueHandler with text-format target."""
        env = {"USERPROFILE": os.environ.get("USERPROFILE", ""), "HOME": os.environ.get("HOME", os.environ.get("USERPROFILE", ""))}
        with patch.dict(os.environ, env, clear=True):
            configure_logging()
            root = logging.getLogger()
            assert len(root.handlers) == 1
            assert isinstance(root.handlers[0], logging.handlers.QueueHandler)

    def test_happy_json_format_when_env_set(self) -> None:
        """JSON format is used on target handlers when LOG_FORMAT=json."""
        with patch.dict(os.environ, {"LOG_FORMAT": "json"}, clear=False):
            configure_logging()
            root = logging.getLogger()
            assert len(root.handlers) == 1
            assert isinstance(root.handlers[0], logging.handlers.QueueHandler)

    def test_happy_level_override(self) -> None:
        """Log level can be overridden by argument."""
        configure_logging(level="DEBUG")
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_happy_level_from_env(self) -> None:
        """Log level reads from LOG_LEVEL env var."""
        with patch.dict(os.environ, {"LOG_LEVEL": "WARNING"}, clear=False):
            configure_logging()
            root = logging.getLogger()
            assert root.level == logging.WARNING

    def test_happy_clears_existing_handlers(self) -> None:
        """Existing handlers are cleared to avoid duplication."""
        root = logging.getLogger()
        root.addHandler(logging.StreamHandler())
        initial_count = len(root.handlers)
        configure_logging()
        assert len(root.handlers) == 1
        assert len(root.handlers) < initial_count or initial_count == 0

    def test_happy_log_dir_from_env(self) -> None:
        """DRAGON_BRAIN_LOG_DIR env var overrides default log directory."""
        import tempfile  # noqa: PLC0415
        from pathlib import Path  # noqa: PLC0415

        td = tempfile.mkdtemp()
        with patch.dict(os.environ, {"DRAGON_BRAIN_LOG_DIR": td}, clear=False):
            configure_logging()
            assert (Path(td) / "dragon-brain.log").exists()
