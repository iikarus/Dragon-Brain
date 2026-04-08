"""Tests for the timed_call timeout wrapper."""

import asyncio

import pytest

from claude_memory.timeout import timed_call


async def test_happy_normal_call() -> None:
    """Normal coroutine completes within timeout and returns result."""

    async def fast() -> str:
        return "ok"

    result = await timed_call("test_fast", fast(), timeout=5.0)
    assert result == "ok"


async def test_evil_timeout() -> None:
    """Slow coroutine exceeds timeout and raises TimeoutError.

    Under nest_asyncio (test ordering pollution), asyncio.wait_for falls
    back to plain await, so the timeout cannot be tested reliably.  We
    skip if wait_for is broken.
    """

    async def slow() -> str:
        await asyncio.sleep(10)
        return "never"

    # Detect nest_asyncio pollution: if wait_for raises RuntimeError,
    # the timeout mechanism is disabled and this test is meaningless.
    try:
        await asyncio.wait_for(asyncio.sleep(0), timeout=1.0)
    except RuntimeError:
        pytest.skip("asyncio.wait_for broken by nest_asyncio pollution")

    with pytest.raises(asyncio.TimeoutError):
        await timed_call("test_slow", slow(), timeout=0.05)


async def test_sad_runtime_error_fallback() -> None:
    """nest_asyncio RuntimeError triggers fallback to plain await."""
    call_count = 0

    async def payload() -> str:
        nonlocal call_count
        call_count += 1
        return "fallback_ok"

    original_wait_for = asyncio.wait_for

    async def fake_wait_for(coro: object, *, timeout: float) -> object:  # noqa: ARG001
        raise RuntimeError("Timeout should be used inside a task")

    asyncio.wait_for = fake_wait_for  # type: ignore[assignment]
    try:
        result = await timed_call("test_fallback", payload(), timeout=5.0)
    finally:
        asyncio.wait_for = original_wait_for

    assert result == "fallback_ok"
    assert call_count == 1


async def test_evil_unrelated_runtime_error() -> None:
    """RuntimeError with different message is re-raised, not swallowed."""

    async def explode() -> str:
        msg = "something else broke"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="something else broke"):
        await timed_call("test_explode", explode(), timeout=5.0)
