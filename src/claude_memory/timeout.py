"""Timeout wrapper for MCP tool handlers.

Provides a single ``timed_call`` function that wraps every MCP tool
invocation with ``asyncio.wait_for`` and duration logging.  Both
``server.py`` and ``tools_extra.py`` import from here to avoid
duplication.
"""

import asyncio
import logging
import time
from typing import Any

MCP_OP_TIMEOUT = 15  # seconds — hard kill for any single tool operation
MCP_OP_TIMEOUT_SEARCH = 30  # seconds — longer timeout for search/hologram operations

_call_logger = logging.getLogger("claude_memory.mcp_calls")


async def timed_call(
    tool_name: str, coro: Any, timeout: float, *, dispatch_t0: float | None = None
) -> Any:
    """Execute an MCP tool call with timeout and duration logging."""
    t0 = time.monotonic()
    if dispatch_t0 is not None:
        wait_ms = (t0 - dispatch_t0) * 1000
        if wait_ms > 500:
            _call_logger.warning("WAIT %-28s %7.0fms pre-dispatch", tool_name, wait_ms)
    try:
        try:
            result = await asyncio.wait_for(coro, timeout=timeout)
        except RuntimeError as exc:
            # nest_asyncio patches the event loop in a way that breaks
            # asyncio.wait_for with "Timeout should be used inside a task".
            # The error is raised BEFORE the coroutine starts, so falling
            # back to a plain ``await`` is safe.
            if "Timeout should be used inside a task" not in str(exc):
                raise
            result = await coro
        elapsed = (time.monotonic() - t0) * 1000
        _call_logger.info("OK  %-28s %7.0fms", tool_name, elapsed)
        return result
    except asyncio.TimeoutError:
        elapsed = (time.monotonic() - t0) * 1000
        _call_logger.error("TIMEOUT %-28s %7.0fms (limit=%ds)", tool_name, elapsed, int(timeout))
        raise
    except Exception as exc:
        elapsed = (time.monotonic() - t0) * 1000
        _call_logger.error("FAIL %-28s %7.0fms %s: %s", tool_name, elapsed, type(exc).__name__, exc)
        raise
