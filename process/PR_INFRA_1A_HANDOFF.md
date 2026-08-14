# PR Handoff: INFRA-1 PR-1A — Truthful Backups (Snapshot-API Qdrant Capture)

**Commit:** `38b36c9528f7c5b5d2a7049d960fd24ce9ce49b9`
**Branch:** `infra-1/backup-snapshot-capture`
**Issue:** INFRA-1 (PR-1A)

---

## Discovery findings

During implementation of INFRA-1 PR-1A (`infra-1/backup-snapshot-capture`), we executed the following scope and verified:
1. **Architect Escalation & Hook Generalization**: Raised pre-flight escalation on `scripts/hooks/branch_write_guard.py` when `ISSUE_BRANCH_PATTERN` did not match the `infra-1/*` branch family. Architect patched `branch_write_guard.py` on master (commit `636cbd8`), generalizing family matching, TOML harness resolution (`INFRA_1_HARNESS.toml` with table `[infra-1]`), and directory-prefix denials.
2. **Snapshot-API Qdrant Capture (B1 / D1 / D2)**: Replaced the legacy live-file tar/docker volume copy of Qdrant RocksDB storage with consistent snapshot capture using sync `QdrantClient(timeout=120)`. The snapshot is created via `client.create_snapshot`, downloaded over HTTP via `httpx.Client` streaming directly to `{target_dir}/qdrant_data.snapshot`, and cleaned up server-side via `client.delete_snapshot`.
3. **Load-Bearing FalkorDB Flush (B2 / D4)**: Updated `_trigger_persistence()` to return `bool`. FalkorDB `SAVE` failure now immediately aborts the backup before archiving any data and exits nonzero (eliminating silent-degradation warn-and-proceed).
4. **Hardened Verification (B3)**: Replaced size-only checks with structural validation:
   - `falkor_data.tar.gz` must open with `tarfile.open` and contain at least 1 member (secondary size warning retained).
   - Qdrant must contain exactly one of `qdrant_data.snapshot` (valid tar archive, >= 10KB) or `qdrant_data.EMPTY` (fresh-install sentinel).
5. **Fresh-Install Sad Path Sentinel (B1 / D5)**: When the `memory_embeddings` collection does not exist, backup creates a zero-byte `qdrant_data.EMPTY` sentinel file, prints an informative note, and succeeds cleanly.
6. **Load-Bearing Exit Codes (B4 / D4)**: `backup()` returns `bool`; CLI `backup_restore.py save` exits 0 on success and 1 on any failure. `scheduled_backup.py` already surfaces nonzero returncodes as `"status": "FAILED"`.
7. **Comprehensive Unit Suite**: Created `tests/unit/test_backup_save.py` with full 8-row coverage (3-evil, 1-sad, 1-neutral per behavior), passing under `-W error` across multi-seed sweeps (0, 1, 42, 1337 and seeds 1-4).

---

## Pre-handoff checklist

| # | Gate | Evidence |
|---|------|----------|
| 1 | `git diff --stat master..HEAD` | `scripts/backup_restore.py      \| 224 +++++++++++++++++-------`<br>`tests/unit/test_backup_save.py \| 389 +++++++++++++++++++++++++++++++++++++++++`<br>`2 files changed, 546 insertions(+), 67 deletions(-)` |
| 2 | `python -m pytest tests/unit/test_backup_save.py -v` | `8 passed in 2.53s` |
| 3 | `python -m mypy --strict src/claude_memory` | `Success: no issues found in 41 source files` |
| 4 | `tox -e contracts` | `SUCCESS: Violations (13) are within baseline (13).` |
| 5 | `python -m bandit -r src/claude_memory -ll` | Verbatim Output:<br>```Test results: >> Issue: [B104:hardcoded_bind_all_interfaces] Possible binding to all interfaces. Severity: Medium Location: src/claude_memory\embedding_server.py:148:26``` (Accepted baseline) |
| 6 | `python -m ruff check src/claude_memory tests scripts` | `All checks passed!` |
| 7 | `git diff --name-only master..HEAD` | ✅ Matches exactly:<br>`scripts/backup_restore.py`<br>`tests/unit/test_backup_save.py` |
| 8 | Two-commit topology | ✅ Commit A (implementation `38b36c9`) and Commit B (handoff) |

---

## Verification Logs

### 1. Canonical ruff check
`python -m ruff check src/claude_memory tests scripts`
```text
All checks passed!
```

### 2. Canonical mypy check
`python -m mypy --strict src/claude_memory`
```text
Success: no issues found in 41 source files
```

### 3. Canonical bandit check
`python -m bandit -r src/claude_memory -ll`
```text
[main]	INFO	profile include tests: None
[main]	INFO	profile exclude tests: None
[main]	INFO	cli include tests: None
[main]	INFO	cli exclude tests: None
[main]	INFO	running on Python 3.12.10
Run started:2026-08-14 16:08:41.062928+00:00

Test results:
>> Issue: [B104:hardcoded_bind_all_interfaces] Possible binding to all interfaces.
   Severity: Medium   Confidence: Medium
   CWE: CWE-605 (https://cwe.mitre.org/data/definitions/605.html)
   More Info: https://bandit.readthedocs.io/en/1.9.3/plugins/b104_hardcoded_bind_all_interfaces.html
   Location: src/claude_memory\embedding_server.py:148:26
147	    port = int(os.getenv("PORT", "8000"))
148	    uvicorn.run(app, host="0.0.0.0", port=port)  # noqa: S104

--------------------------------------------------

Code scanned:
	Total lines of code: 7110
	Total lines skipped (#nosec): 0
	Total potential issues skipped due to specifically being disabled (e.g., #nosec BXXX): 2

Run metrics:
	Total issues (by severity):
		Undefined: 0
		Low: 1
		Medium: 1
		High: 0
	Total issues (by confidence):
		Undefined: 0
		Low: 0
		Medium: 1
		High: 1
Files skipped (0):
```

### 4. Canonical contracts check
`tox -e contracts`
```text
[1/1] Contract Scanner...
contracts: commands[1]> python scripts/trace_contracts_dragon.py src/claude_memory --baseline 13
Dragon Brain Contract Scanner — Audit Edition
============================================================

Scanned 138 files. Found 13 violations.

By category:
  Bare Pass: 6
  Silent Fallback: 5
  Per-Item Swallow: 2

Report saved to contract_violations_report.md

SUCCESS: Violations (13) are within baseline (13).
  contracts: OK (12.61=setup[12.22]+cmd[0.05,0.34] seconds)
  congratulations :) (12.67 seconds)
```

---

## Multi-seed sweep evidence (Checklist requirement)

The complete PR-1A test suite runs clean and outputs warning-free executions under `-W error` across multi-seed executions.

### Seed 1 Sweep (seed=1)
`python -m pytest tests/unit/test_backup_save.py --randomly-seed=1 -v`
```text
Using --randomly-seed=1
rootdir: C:\Users\Asus\.gemini\antigravity\scratch\new_project\claude-memory-mcp
configfile: pyproject.toml
plugins: anyio-4.12.0, hypothesis-6.151.5, asyncio-1.3.0, benchmark-5.2.3, cov-7.0.0, forked-1.6.0, randomly-4.0.1, timeout-2.4.0, xdist-3.8.0, schemathesis-4.9.5, syrupy-5.1.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 8 items

tests/unit/test_backup_save.py::test_neutral_happy_path PASSED           [ 12%]
tests/unit/test_backup_save.py::test_evil_download_yields_truncated_file PASSED [ 25%]
tests/unit/test_backup_save.py::test_evil_falkor_corrupt_gzip PASSED     [ 37%]
tests/unit/test_backup_save.py::test_sad_collection_does_not_exist PASSED [ 50%]
tests/unit/test_backup_save.py::test_neutral_verify_backup_well_formed PASSED [ 62%]
tests/unit/test_backup_save.py::test_evil_falkordb_save_raises PASSED    [ 75%]
tests/unit/test_backup_save.py::test_evil_snapshot_delete_raises_after_good_download PASSED [ 87%]
tests/unit/test_backup_save.py::test_evil_create_snapshot_raises PASSED  [100%]

============================== 8 passed in 3.83s ==============================
```

### Seed 2 Sweep (seed=2)
`python -m pytest tests/unit/test_backup_save.py --randomly-seed=2 -v`
```text
Using --randomly-seed=2
rootdir: C:\Users\Asus\.gemini\antigravity\scratch\new_project\claude-memory-mcp
configfile: pyproject.toml
plugins: anyio-4.12.0, hypothesis-6.151.5, asyncio-1.3.0, benchmark-5.2.3, cov-7.0.0, forked-1.6.0, randomly-4.0.1, timeout-2.4.0, xdist-3.8.0, schemathesis-4.9.5, syrupy-5.1.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 8 items

tests/unit/test_backup_save.py::test_evil_falkor_corrupt_gzip PASSED     [ 12%]
tests/unit/test_backup_save.py::test_evil_snapshot_delete_raises_after_good_download PASSED [ 25%]
tests/unit/test_backup_save.py::test_neutral_verify_backup_well_formed PASSED [ 37%]
tests/unit/test_backup_save.py::test_sad_collection_does_not_exist PASSED [ 50%]
tests/unit/test_backup_save.py::test_evil_falkordb_save_raises PASSED    [ 62%]
tests/unit/test_backup_save.py::test_evil_create_snapshot_raises PASSED  [ 75%]
tests/unit/test_backup_save.py::test_evil_download_yields_truncated_file PASSED [ 87%]
tests/unit/test_backup_save.py::test_neutral_happy_path PASSED           [100%]

============================== 8 passed in 3.73s ==============================
```

### Seed 3 Sweep (seed=3)
`python -m pytest tests/unit/test_backup_save.py --randomly-seed=3 -v`
```text
Using --randomly-seed=3
rootdir: C:\Users\Asus\.gemini\antigravity\scratch\new_project\claude-memory-mcp
configfile: pyproject.toml
plugins: anyio-4.12.0, hypothesis-6.151.5, asyncio-1.3.0, benchmark-5.2.3, cov-7.0.0, forked-1.6.0, randomly-4.0.1, timeout-2.4.0, xdist-3.8.0, schemathesis-4.9.5, syrupy-5.1.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 8 items

tests/unit/test_backup_save.py::test_sad_collection_does_not_exist PASSED [ 12%]
tests/unit/test_backup_save.py::test_evil_snapshot_delete_raises_after_good_download PASSED [ 25%]
tests/unit/test_backup_save.py::test_evil_download_yields_truncated_file PASSED [ 37%]
tests/unit/test_backup_save.py::test_neutral_verify_backup_well_formed PASSED [ 50%]
tests/unit/test_backup_save.py::test_evil_falkordb_save_raises PASSED    [ 62%]
tests/unit/test_backup_save.py::test_evil_create_snapshot_raises PASSED  [ 75%]
tests/unit/test_backup_save.py::test_evil_falkor_corrupt_gzip PASSED     [ 87%]
tests/unit/test_backup_save.py::test_neutral_happy_path PASSED           [100%]

============================== 8 passed in 3.89s ==============================
```

### Seed 4 Sweep (seed=4)
`python -m pytest tests/unit/test_backup_save.py --randomly-seed=4 -v`
```text
Using --randomly-seed=4
rootdir: C:\Users\Asus\.gemini\antigravity\scratch\new_project\claude-memory-mcp
configfile: pyproject.toml
plugins: anyio-4.12.0, hypothesis-6.151.5, asyncio-1.3.0, benchmark-5.2.3, cov-7.0.0, forked-1.6.0, randomly-4.0.1, timeout-2.4.0, xdist-3.8.0, schemathesis-4.9.5, syrupy-5.1.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 8 items

tests/unit/test_backup_save.py::test_sad_collection_does_not_exist PASSED [ 12%]
tests/unit/test_backup_save.py::test_evil_falkordb_save_raises PASSED    [ 25%]
tests/unit/test_backup_save.py::test_evil_create_snapshot_raises PASSED  [ 37%]
tests/unit/test_backup_save.py::test_evil_falkor_corrupt_gzip PASSED     [ 50%]
tests/unit/test_backup_save.py::test_evil_download_yields_truncated_file PASSED [ 62%]
tests/unit/test_backup_save.py::test_neutral_happy_path PASSED           [ 75%]
tests/unit/test_backup_save.py::test_neutral_verify_backup_well_formed PASSED [ 87%]
tests/unit/test_backup_save.py::test_evil_snapshot_delete_raises_after_good_download PASSED [100%]

============================== 8 passed in 3.82s ==============================
```
