"""Mutation-killing tests for lock_manager.py — ProjectLock context manager, env chains.

Targets ~15 kills: ProjectLock project_id propagation, acquire/release,
async context manager, release-on-exception.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

# ═══════════════════════════════════════════════════════════════════
# ProjectLock — project_id and manager propagation
# ═══════════════════════════════════════════════════════════════════


class TestProjectLockConstruction:
    """Assert ProjectLock stores correct project_id and manager ref."""

    def test_happy_lock_evil_project_id_propagated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Evil: ProjectLock must store the exact project_id."""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        monkeypatch.delenv("REDIS_PORT", raising=False)
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
        monkeypatch.delenv("FALKORDB_HOST", raising=False)
        monkeypatch.delenv("FALKORDB_PORT", raising=False)
        monkeypatch.delenv("FALKORDB_PASSWORD", raising=False)

        with patch("claude_memory.lock_manager.redis.Redis") as mock_redis:
            mock_redis.return_value.ping.return_value = True
            from claude_memory.lock_manager import LockManager

            lm = LockManager()
            lock = lm.lock("test-project")
            assert lock.project_id == "test-project"

    def test_happy_lock_evil_manager_bound(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Evil: ProjectLock must store reference to the LockManager."""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        monkeypatch.delenv("REDIS_PORT", raising=False)
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
        monkeypatch.delenv("FALKORDB_HOST", raising=False)
        monkeypatch.delenv("FALKORDB_PORT", raising=False)
        monkeypatch.delenv("FALKORDB_PASSWORD", raising=False)

        with patch("claude_memory.lock_manager.redis.Redis") as mock_redis:
            mock_redis.return_value.ping.return_value = True
            from claude_memory.lock_manager import LockManager

            lm = LockManager()
            lock = lm.lock("p1")
            assert lock.manager is lm

    def test_happy_lock_evil_different_projects(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Evil: different project IDs produce different ProjectLock objects."""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        monkeypatch.delenv("REDIS_PORT", raising=False)
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
        monkeypatch.delenv("FALKORDB_HOST", raising=False)
        monkeypatch.delenv("FALKORDB_PORT", raising=False)
        monkeypatch.delenv("FALKORDB_PASSWORD", raising=False)

        with patch("claude_memory.lock_manager.redis.Redis") as mock_redis:
            mock_redis.return_value.ping.return_value = True
            from claude_memory.lock_manager import LockManager

            lm = LockManager()
            lock_a = lm.lock("project-a")
            lock_b = lm.lock("project-b")
            assert lock_a.project_id != lock_b.project_id

    def test_sad1_lock_sad_empty_project_id(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sad: empty project_id is technically valid."""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        monkeypatch.delenv("REDIS_PORT", raising=False)
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
        monkeypatch.delenv("FALKORDB_HOST", raising=False)
        monkeypatch.delenv("FALKORDB_PORT", raising=False)
        monkeypatch.delenv("FALKORDB_PASSWORD", raising=False)

        with patch("claude_memory.lock_manager.redis.Redis") as mock_redis:
            mock_redis.return_value.ping.return_value = True
            from claude_memory.lock_manager import LockManager

            lm = LockManager()
            lock = lm.lock("")
            assert lock.project_id == ""

    def test_happy_lock_happy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy: ProjectLock constructed correctly."""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        monkeypatch.delenv("REDIS_PORT", raising=False)
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
        monkeypatch.delenv("FALKORDB_HOST", raising=False)
        monkeypatch.delenv("FALKORDB_PORT", raising=False)
        monkeypatch.delenv("FALKORDB_PASSWORD", raising=False)

        with patch("claude_memory.lock_manager.redis.Redis") as mock_redis:
            mock_redis.return_value.ping.return_value = True
            from claude_memory.lock_manager import LockManager

            lm = LockManager()
            lock = lm.lock("my-proj")
            assert lock.project_id == "my-proj"
            assert lock.manager is lm


# ═══════════════════════════════════════════════════════════════════
# ProjectLock — Async Context Manager
# ═══════════════════════════════════════════════════════════════════


class TestProjectLockAsyncCtx:
    """Assert async context manager calls acquire/release."""

    async def test_happy_ctx_evil_acquire_called(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Evil: __aenter__ must trigger acquire."""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        monkeypatch.delenv("REDIS_PORT", raising=False)
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
        monkeypatch.delenv("FALKORDB_HOST", raising=False)
        monkeypatch.delenv("FALKORDB_PORT", raising=False)
        monkeypatch.delenv("FALKORDB_PASSWORD", raising=False)

        with patch("claude_memory.lock_manager.redis.Redis") as mock_redis:
            mock_redis.return_value.ping.return_value = True
            mock_redis.return_value.set.return_value = True
            from claude_memory.lock_manager import LockManager

            lm = LockManager()
            async with lm.lock("p1"):
                mock_redis.return_value.set.assert_called()

    async def test_happy_ctx_evil_release_called(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Evil: __aexit__ must trigger release (Redis DELETE)."""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        monkeypatch.delenv("REDIS_PORT", raising=False)
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
        monkeypatch.delenv("FALKORDB_HOST", raising=False)
        monkeypatch.delenv("FALKORDB_PORT", raising=False)
        monkeypatch.delenv("FALKORDB_PASSWORD", raising=False)

        with patch("claude_memory.lock_manager.redis.Redis") as mock_redis:
            mock_redis.return_value.ping.return_value = True
            mock_redis.return_value.set.return_value = True
            from claude_memory.lock_manager import LockManager

            lm = LockManager()
            async with lm.lock("p1"):
                pass
            mock_redis.return_value.delete.assert_called()

    async def test_evil1_ctx_evil_release_on_exception(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Evil: lock must be released even if body raises."""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        monkeypatch.delenv("REDIS_PORT", raising=False)
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
        monkeypatch.delenv("FALKORDB_HOST", raising=False)
        monkeypatch.delenv("FALKORDB_PORT", raising=False)
        monkeypatch.delenv("FALKORDB_PASSWORD", raising=False)

        with patch("claude_memory.lock_manager.redis.Redis") as mock_redis:
            mock_redis.return_value.ping.return_value = True
            mock_redis.return_value.set.return_value = True
            from claude_memory.lock_manager import LockManager

            lm = LockManager()
            with pytest.raises(RuntimeError):
                async with lm.lock("p1"):
                    raise RuntimeError("boom")
            mock_redis.return_value.delete.assert_called()

    async def test_evil2_ctx_sad_timeout_on_contention(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sad: if acquire always fails, TimeoutError is raised."""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        monkeypatch.delenv("REDIS_PORT", raising=False)
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
        monkeypatch.delenv("FALKORDB_HOST", raising=False)
        monkeypatch.delenv("FALKORDB_PORT", raising=False)
        monkeypatch.delenv("FALKORDB_PASSWORD", raising=False)

        with patch("claude_memory.lock_manager.redis.Redis") as mock_redis:
            mock_redis.return_value.ping.return_value = True
            mock_redis.return_value.set.return_value = False  # always fail
            from claude_memory.lock_manager import LockManager

            lm = LockManager()
            with patch("asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(TimeoutError):
                    async with lm.lock("p1"):
                        pass

    async def test_happy_ctx_happy_normal_flow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Happy: acquire → execute → release completes normally."""
        monkeypatch.delenv("REDIS_HOST", raising=False)
        monkeypatch.delenv("REDIS_PORT", raising=False)
        monkeypatch.delenv("REDIS_PASSWORD", raising=False)
        monkeypatch.delenv("FALKORDB_HOST", raising=False)
        monkeypatch.delenv("FALKORDB_PORT", raising=False)
        monkeypatch.delenv("FALKORDB_PASSWORD", raising=False)

        with patch("claude_memory.lock_manager.redis.Redis") as mock_redis:
            mock_redis.return_value.ping.return_value = True
            mock_redis.return_value.set.return_value = True
            from claude_memory.lock_manager import LockManager

            lm = LockManager()
            executed = False
            async with lm.lock("p1"):
                executed = True
            assert executed
