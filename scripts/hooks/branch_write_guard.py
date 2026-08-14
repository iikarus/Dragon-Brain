#!/usr/bin/env python3
"""Pre-commit hook: deny write access to specific paths on issue branches.

Implements the Topographical Forcing pattern from the AI Council trifecta
workflow (per Deepthink consult, 2026-05-20). When the current branch matches
an issue pattern (e.g. `issue-14/*`), reads the per-issue denylist from
`process/issues/<N>_HARNESS.toml` and blocks any staged change that touches a
denylisted path.

The point is to **physically prevent** the Builder from reaching for the
shortcut escape routes documented by the Architect for THIS specific PR,
rather than rely on negative-prompt instructions (which are
attention-injection antipatterns per Ironic Process Theory).

Example denylist for issue-14 (warning-suppression masking class):

    # process/issues/14_HARNESS.toml
    [issue-14]
    denied_paths = [
        "tests/unit/conftest.py",
        "tests/conftest.py",
        "pytest.ini",
    ]
    rationale = "Issue #14 requires source-level MagicMock -> AsyncMock fixes. \
        Modifying conftest.py risks adding warning-suppression fixtures \
        instead of fixing the underlying mocks. Modify the test files only."

If a denied path is staged, the hook exits non-zero with the rationale.

If the branch doesn't match a family pattern, hook is a no-op.

Branch families (generalized 2026-08-14 for the INFRA arc):
  - `issue-<N><letter?>/...` → harness `process/issues/<N>_HARNESS.toml`,
    table `[issue-<N>]` (legacy naming, e.g. issue-22f → 22_HARNESS.toml)
  - `<family>-<N><letter?>/...` → harness
    `process/issues/<FAMILY>_<N>_HARNESS.toml`, table `[<family>-<N>]`
    (e.g. infra-1a → INFRA_1_HARNESS.toml, table [infra-1])

Denylist entries match exact file paths OR directory prefixes: an entry
`src/claude_memory` blocks any staged file under that directory. (Prior to
2026-08-14 matching was exact-string only, which made directory entries
silently inert — staged files never string-equal their parent directory.)
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

BRANCH_FAMILY_PATTERN = re.compile(r"^([a-z]+)-(\d+)[a-z]?(?:_bis|-bis)?(?:/|$)")


def get_current_branch() -> str | None:
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip() or None
    except subprocess.CalledProcessError:
        return None


def get_staged_files() -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()]


def load_harness_config(family: str, num: str) -> dict[str, object] | None:
    """Resolve the harness for a branch family, supporting both naming conventions.

    Legacy (issue-N):   process/issues/<N>_HARNESS.toml, table [issue-<N>]
    General (family-N): process/issues/<FAMILY>_<N>_HARNESS.toml, table [<family>-<N>]
    """
    table_name = f"{family}-{num}"
    candidates = [
        Path("process/issues") / f"{num}_HARNESS.toml",
        Path("process/issues") / f"{family.upper()}_{num}_HARNESS.toml",
    ]
    for config_path in candidates:
        if not config_path.exists():
            continue
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
        section = data.get(table_name)
        if section is not None:
            return section
    return None


def is_denied(path: str, denied_paths: list[str]) -> bool:
    """True if path exactly matches a denylist entry or lies under a denied directory."""
    return any(path == entry or path.startswith(entry.rstrip("/") + "/") for entry in denied_paths)


def main() -> int:
    branch = get_current_branch()
    if not branch:
        return 0

    match = BRANCH_FAMILY_PATTERN.match(branch)
    if not match:
        # Not a family branch — no constraints apply
        return 0

    family, num = match.group(1), match.group(2)
    config = load_harness_config(family, num)
    if not config:
        # Family branch but no harness config exists for it
        # → no constraints (this is intentional; not all arcs need a harness)
        return 0

    denied_paths = config.get("denied_paths", [])
    rationale = config.get("rationale", "(no rationale provided)")
    if not denied_paths:
        return 0

    staged = get_staged_files()
    violations = [f for f in staged if is_denied(f, denied_paths)]
    if not violations:
        return 0

    print(
        f"\nbranch-write-guard: BLOCKED — {family}-{num} denies modification "
        f"to the following paths:\n",
        file=sys.stderr,
    )
    for v in violations:
        print(f"  - {v}", file=sys.stderr)
    print(f"\nRationale (per the {family}-{num} harness TOML):", file=sys.stderr)
    print(f"  {rationale}\n", file=sys.stderr)
    print(
        "If you genuinely need to modify a denied path, escalate to the "
        "Architect for a spec revision. Do NOT bypass this guard.\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
