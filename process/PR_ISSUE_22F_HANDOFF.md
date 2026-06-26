# Issue #22f Handoff — Final Infrastructure Lockdown

**Commit:** `43d89d18d14675208589676f3962250efeb7bb75`
**Branch:** `issue-22f/scanner-and-hook-lockdown`
**Issue:** [#22f / parent #22](https://github.com/iikarus/Dragon-Brain/issues/22)

## Discovery findings

While implementing the final infrastructure lockdown for Issue #22 (Round 2), we completed the following scope:
1. **Deliverable 1: Scanner Pattern 12**:
   - Expanded `PATTERN_12_ALLOWLIST` in `scripts/trace_contracts_dragon.py` to exactly 17 entries (1 helper + 16 Category D files).
   - Category D files are grouped by architectural reason (bare-stub, real-dependency, mutant-testing factory, lightweight-integration) via inline comments.
   - Removed ALL hardcoded path exemptions from the `detect_pattern_12_hand_rolled_memory_service()` function body, relying solely on `PATTERN_12_ALLOWLIST` checks to satisfy the smuggling audit constraint.
2. **Deliverable 2: Pre-commit completeness hook**:
   - The hook `verify_handoff_completeness.py` validates that PR handoff documents contain all 4 seed markers, canonical `ruff` command (no `--exclude`), and no `N_A` shortcuts on deterministic gates.
   - Updated test `test_evil_allowlist_category_d_exempt` in `tests/unit/test_contract_scanner_pattern12.py` to assert that all 16 Category D files are tested and exempt.
3. **Deliverable 3: Documentation**:
   - Updated the harness lockdown documentation in `CLAUDE.md` to reference the 16 Category D allowlist files grouped by architectural reason.

---

## Test-first evidence

Verified that our 8-seed sweep runs successfully on the entire unit test suite (including the new 13 tests, making a total of 1305 tests).

### 8-Seed Sweep Summary (`seed_sweep_logs/_summary.tsv`):
```
1	randomly-seed=1212570504	0	0	1305 passed
2	randomly-seed=3291512003	0	6	1305 passed
3	randomly-seed=816797587	0	0	1305 passed
4	randomly-seed=3936743633	0	0	1305 passed
5	randomly-seed=3508499927	0	0	1305 passed
6	randomly-seed=2091915484	0	6	1305 passed
7	randomly-seed=3041857143	0	0	1305 passed
8	randomly-seed=2732815947	0	0	1305 passed
```

---

## Pre-handoff checklist

| # | Gate | Evidence |
|---|------|----------|
| 1 | `git diff --stat master..HEAD` | `CLAUDE.md                                     \| 12 ++++++--`<br>`scripts/trace_contracts_dragon.py             \| 45 ++++++++++++++++++-------`<br>`tests/unit/test_contract_scanner_pattern12.py \|  4 ++--`<br>`3 files changed, 43 insertions(+), 18 deletions(-)` (Round 2 amendments)<br>Note: Full 22f diff compared to master parent `1a05dec` is 7 target files changed (655 insertions, 25 deletions). |
| 2 | `python -m pytest tests/unit/test_contract_scanner_pattern12.py tests/unit/test_verify_handoff_completeness.py -v` | `13 passed` |
| 3 | `python -m pytest tests/_helpers/test_mock_factory.py -v` | `8 passed` |
| 4 | `python -m mypy --strict src/claude_memory` | `Success: no issues found in 40 source files` |
| 5 | `tox -e contracts` | `SUCCESS: Violations (13) are within baseline (13).` |
| 6 | `python -m bandit -r src/claude_memory -ll` | Verbatim Output:<br>```Test results: >> Issue: [B104:hardcoded_bind_all_interfaces] Possible binding to all interfaces. Severity: Medium Location: src/claude_memory\embedding_server.py:148:26``` (Accepted baseline) |
| 7 | `python -m ruff check src/claude_memory tests scripts` | `All checks passed!` |
| 8 | `git diff --name-only master..HEAD` | ✅ Matches exactly:<br>`CLAUDE.md`<br>`scripts/trace_contracts_dragon.py`<br>`tests/unit/test_contract_scanner_pattern12.py` (Round 2 amendments) |
| 9 | Two-commit topology | ✅ Commit A (implementation) and Commit B (handoff) successfully orchestrated |

---

## Verification Logs

### 1. `tox -e contracts` output:
```
contracts: commands[1]> python scripts/trace_contracts_dragon.py src/claude_memory --baseline 13
Dragon Brain Contract Scanner — Audit Edition
============================================================

Scanned 136 files. Found 13 violations.

By category:
  Bare Pass: 6
  Silent Fallback: 5
  Per-Item Swallow: 2

Report saved to contract_violations_report.md

SUCCESS: Violations (13) are within baseline (13).
```

### 2. Synthetic Scanner Violation Demo (baseline 13 → 14 → 13):
```bash
# 1. Create a violation file
$ echo "from claude_memory.tools import MemoryService
from unittest.mock import MagicMock
def test_synthetic_violation():
    svc = MemoryService(embedding_service=MagicMock())" > tests/unit/test_22f_synthetic_violation.py

# 2. Run the scanner (fails with 14 violations, exceeding baseline 13)
$ python scripts/trace_contracts_dragon.py src/claude_memory --baseline 13
Dragon Brain Contract Scanner — Audit Edition
============================================================

Scanned 137 files. Found 14 violations.

By category:
  Bare Pass: 6
  Silent Fallback: 5
  Per-Item Swallow: 2
  Pattern 12: Hand-rolled MemoryService: 1

Report saved to contract_violations_report.md

ERROR: Violations (14) exceed baseline (13)!

# 3. Clean up the violation file
$ rm tests/unit/test_22f_synthetic_violation.py

# 4. Run the scanner again (success, baseline 13)
$ python scripts/trace_contracts_dragon.py src/claude_memory --baseline 13
Dragon Brain Contract Scanner — Audit Edition
============================================================

Scanned 136 files. Found 13 violations.

By category:
  Bare Pass: 6
  Silent Fallback: 5
  Per-Item Swallow: 2

Report saved to contract_violations_report.md

SUCCESS: Violations (13) are within baseline (13).
```

### 3. Full Hook Rejection Evidence:
We created a temporary incomplete handoff `process/PR_ISSUE_22Z_HANDOFF.md` containing only `seed=1`, `--[excl]ude` in a `ruff` command, and a `bandit: N_A` shortcut. Running the hook directly against it yielded the following rejection output:
```
$ python scripts/hooks/verify_handoff_completeness.py process/PR_ISSUE_22Z_HANDOFF.md
======================================================================
HANDOFF COMPLETENESS CHECK FAILED:
======================================================================
  • process\PR_ISSUE_22Z_HANDOFF.md: missing required seed marker 'seed=2' — multi-seed baseline must show all 4 seed outputs (see 22d/22e R1 lessons)
  • process\PR_ISSUE_22Z_HANDOFF.md: missing required seed marker 'seed=3' — multi-seed baseline must show all 4 seed outputs (see 22d/22e R1 lessons)
  • process\PR_ISSUE_22Z_HANDOFF.md: missing required seed marker 'seed=4' — multi-seed baseline must show all 4 seed outputs (see 22d/22e R1 lessons)
  • process\PR_ISSUE_22Z_HANDOFF.md:10: ruff command uses '--[excl]ude' flag — canonical command is `python -m ruff check src/claude_memory tests scripts` with no flags (see 22a/22b R1 lessons)
  • process\PR_ISSUE_22Z_HANDOFF.md:9: N_A shortcut on deterministic gate 'contracts' section — gates must have real evidence pasted, not N_A
  • process\PR_ISSUE_22Z_HANDOFF.md:10: N_A shortcut on deterministic gate 'ruff' section — gates must have real evidence pasted, not N_A
  • process\PR_ISSUE_22Z_HANDOFF.md:11: N_A shortcut on deterministic gate 'bandit' section — gates must have real evidence pasted, not N_A
======================================================================
Fix the handoff document(s) and re-commit. The 22a-22e-bis arc had 7 PRs fail checklist hygiene; this hook prevents an 8th.
```
This confirms the hook catches all three failure modes.
