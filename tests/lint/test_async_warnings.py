"""Regression suite: verify the codebase emits no RuntimeWarnings under strict gate.

These tests run pytest as a subprocess against specific test files (or the
full suite) with ``-W error::RuntimeWarning`` and verify no coroutine-related
warnings appear in stdout or stderr.

Note: Python wraps unawaited-coroutine warnings in
``PytestUnraisableExceptionWarning``, so ``-W error::RuntimeWarning`` alone
does NOT make pytest exit non-zero.  We scan combined output for TWO sentinel
strings:

- ``RuntimeWarning: coroutine`` — the raw Python warning
- ``PytestUnraisableExceptionWarning`` — pytest's wrapper for GC-time warnings

Either sentinel means an unawaited coroutine leaked.

Slow by design (each test re-invokes pytest). Run via ``tox -e lint-warnings``
or directly via ``pytest tests/lint/ -q`` — kept out of the main suite for speed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore[no-redef]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WARNING_SENTINELS = [
    "RuntimeWarning: coroutine",
    "PytestUnraisableExceptionWarning",
]


def _run_pytest_strict(targets: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", *targets, "-W", "error::RuntimeWarning", "-q", "--tb=no"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=900,
        check=False,
    )


def _has_coroutine_warnings(result: subprocess.CompletedProcess[str]) -> str:
    """Return warning lines if any coroutine-related warnings found."""
    combined = result.stdout + "\n" + result.stderr
    warning_lines = [
        line
        for line in combined.splitlines()
        if any(sentinel in line for sentinel in WARNING_SENTINELS)
    ]
    return "\n".join(warning_lines)


def test_evil_full_unit_suite_no_runtime_warnings() -> None:
    """Full unit suite runs clean — no coroutine RuntimeWarnings in output."""
    result = _run_pytest_strict(["tests/unit/"])
    warnings = _has_coroutine_warnings(result)
    assert not warnings, (
        f"Unit suite emitted coroutine RuntimeWarning(s):\n{warnings}\n"
        f"stdout (last 50 lines):\n{result.stdout[-3000:]}\n"
        f"stderr (last 20 lines):\n{result.stderr[-1500:]}"
    )


def test_evil_test_tools_coverage_no_runtime_warnings() -> None:
    """test_tools_coverage.py runs clean (PR-6 Discovery target)."""
    result = _run_pytest_strict(["tests/unit/test_tools_coverage.py"])
    warnings = _has_coroutine_warnings(result)
    assert not warnings, f"Warnings in test_tools_coverage.py:\n{warnings}\n{result.stderr[-2000:]}"


def test_evil_test_memory_service_no_runtime_warnings() -> None:
    """test_memory_service.py runs clean (PR-6 Discovery target)."""
    result = _run_pytest_strict(["tests/unit/test_memory_service.py"])
    warnings = _has_coroutine_warnings(result)
    assert not warnings, f"Warnings in test_memory_service.py:\n{warnings}\n{result.stderr[-2000:]}"


def test_sad_pytest_filterwarnings_config_present() -> None:
    """pyproject.toml contains filterwarnings = [\"error::RuntimeWarning\"]."""
    pyproject_path = REPO_ROOT / "pyproject.toml"
    assert pyproject_path.exists(), "pyproject.toml not found at repo root"
    with open(pyproject_path, "rb") as f:
        config = tomllib.load(f)
    pytest_opts = config.get("tool", {}).get("pytest", {}).get("ini_options", {})
    filterwarnings = pytest_opts.get("filterwarnings", [])
    assert "error::RuntimeWarning" in filterwarnings, (
        f"Expected 'error::RuntimeWarning' in filterwarnings, got: {filterwarnings}"
    )
