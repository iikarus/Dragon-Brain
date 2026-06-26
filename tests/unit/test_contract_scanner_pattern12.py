"""Acceptance tests for Pattern 12 (hand-rolled MemoryService detection).

Per process/issues/22f_BUILD_SPEC.md.
"""

import ast
from textwrap import dedent

from scripts.trace_contracts_dragon import (
    PATTERN_12_ALLOWLIST,
    detect_pattern_12_hand_rolled_memory_service,
)

# ─── Allowlist tests ───────────────────────────────────────────────────


def test_evil_allowlist_helper_exempt() -> None:
    """The helper at tests/_helpers/mock_factory.py is allowlisted — must not fire."""
    source = dedent("""
        from claude_memory.tools import MemoryService
        svc = MemoryService(embedding_service=embedder)
    """)
    tree = ast.parse(source)
    violations = detect_pattern_12_hand_rolled_memory_service(
        tree, "tests/_helpers/mock_factory.py"
    )
    assert violations == []


def test_evil_allowlist_category_d_exempt() -> None:
    """All 16 Category D files are allowlisted — none should fire."""
    source = dedent("""
        from claude_memory.tools import MemoryService
        svc = MemoryService(embedding_service=embedder, vector_store=vs)
    """)
    tree = ast.parse(source)
    category_d_files = [f for f in PATTERN_12_ALLOWLIST if f != "tests/_helpers/mock_factory.py"]
    assert len(category_d_files) == 16, "Expected 16 Category D files in allowlist"
    for filepath in category_d_files:
        violations = detect_pattern_12_hand_rolled_memory_service(tree, filepath)
        assert violations == [], f"FAIL: Category D file {filepath} fired Pattern 12"


# ─── Detection tests ────────────────────────────────────────────────────


def test_evil_detects_hand_rolled_in_non_allowlisted_file() -> None:
    """A non-allowlisted file with hand-rolled construction must fire Pattern 12."""
    source = dedent("""
        from claude_memory.tools import MemoryService
        svc = MemoryService(embedding_service=embedder)
    """)
    tree = ast.parse(source)
    violations = detect_pattern_12_hand_rolled_memory_service(
        tree, "tests/unit/test_some_new_file.py"
    )
    assert len(violations) == 1
    assert "Pattern 12" in violations[0][4]


def test_evil_detects_attribute_access_call() -> None:
    """`tools.MemoryService(...)` (Attribute access) also fires."""
    source = dedent("""
        from claude_memory import tools
        svc = tools.MemoryService(embedding_service=embedder)
    """)
    tree = ast.parse(source)
    violations = detect_pattern_12_hand_rolled_memory_service(
        tree, "tests/unit/test_some_new_file.py"
    )
    assert len(violations) == 1


def test_evil_detects_multiple_inline_constructions() -> None:
    """File with N inline constructions emits N violations (test_batch5-style regression)."""
    source = dedent("""
        from claude_memory.tools import MemoryService

        async def test_one():
            svc = MemoryService(embedding_service=MagicMock())

        async def test_two():
            svc = MemoryService(embedding_service=MagicMock(), vector_store=AsyncMock())

        async def test_three():
            svc = MemoryService(embedding_service=mock)
    """)
    tree = ast.parse(source)
    violations = detect_pattern_12_hand_rolled_memory_service(tree, "tests/unit/test_regression.py")
    assert len(violations) == 3


# ─── Sad-path / neutral tests ──────────────────────────────────────────


def test_sad_no_embedding_service_kwarg_does_not_fire() -> None:
    """A `MemoryService(...)` call WITHOUT `embedding_service=` kwarg doesn't fire.

    Defensive filter against false positives on unrelated MemoryService-named
    symbols or alternative call signatures.
    """
    source = dedent("""
        class MemoryService:
            pass

        svc = MemoryService()  # no embedding_service kwarg
    """)
    tree = ast.parse(source)
    violations = detect_pattern_12_hand_rolled_memory_service(tree, "tests/unit/test_unrelated.py")
    assert violations == []


def test_neutral_empty_file_no_violations() -> None:
    """Empty file emits no violations."""
    tree = ast.parse("")
    violations = detect_pattern_12_hand_rolled_memory_service(tree, "tests/unit/test_empty.py")
    assert violations == []
