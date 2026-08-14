# PR Handoff: INFRA-1 PR-1B — Dual-Format Restore & Snapshot Leak Closure

**Commit:** `48868615acb7d2f169142bd523015ce299ab795f`
**Branch:** `infra-1/restore-dual-format`
**Issue:** INFRA-1 (PR-1B)

---

## Discovery findings

During implementation of INFRA-1 PR-1B (`infra-1/restore-dual-format`), we executed the following scope and verified:
1. **Dual-Format Restore Sequence (B5 / D3 / D5)**: Replaced unverified restore logic with a strict, pre-flight validated restore sequence:
   - **Pre-flight artifact validation (B5.1)**: Validates that `falkor_data.tar.gz` exists and exactly one Qdrant artifact format (`qdrant_data.snapshot`, `qdrant_data.EMPTY`, or legacy `qdrant_data.tar.gz`) is present BEFORE touching containers. Refuses execution immediately without stopping containers if invalid.
   - **Dual-format precedence**: If both `.snapshot` and legacy `qdrant_data.tar.gz` are present, prefers `.snapshot` and emits a clear `[WARN]`.
   - **FalkorDB Volume Restore (B5.3)**: Restores RDB volume and inspects return code.
   - **Legacy Format (B5.4)**: Executes volume wipe+untar and checks subprocess return codes per B6 before starting containers.
   - **Snapshot Format (B5.5)**: Starts containers, polls Qdrant readiness via `_wait_for_qdrant` (up to 60s), uploads and recovers collection snapshot via multipart `POST /collections/{collection}/snapshots/upload?priority=snapshot`, and verifies collection presence.
   - **.EMPTY Sentinel Format (B5.6)**: Starts containers, prints note, and cleanly skips Qdrant recovery.
   - **Post-Restore Verification (B5.7)**: Ensures collection is listed in `get_collections()` and prints point count via `client.count()`.
2. **Subprocess Exit-Code Contract (B6 / D4)**: Inspected and enforced exit code checks on all Docker and Docker-Compose commands (`docker-compose stop`, `docker-compose up -d`, FalkorDB docker untar, Qdrant legacy docker untar). No command can fail silently or print `[OK]` upon failure.
3. **Snapshot Leak Window Closure (B7)**: Updated `_snapshot_qdrant` in `scripts/backup_restore.py` so that if an HTTP error, socket timeout, or stream failure occurs during download after `create_snapshot` succeeded, a `client.delete_snapshot` call is attempted before returning False, preventing server-side snapshot accumulation on Qdrant.
4. **Comprehensive Unit Suite**:
   - Added `tests/unit/test_backup_load.py` covering the 8 restore scenarios (rows 1-8 of test design matrix).
   - Added `test_evil_download_fails_cleans_up_snapshot` in `tests/unit/test_backup_save.py` (row 9 / B7).
   - All 17 tests pass under `-W error` across multi-seed sweeps (seeds 0, 1, 42, 1337 and seeds 1-4).

---

## Pre-handoff checklist

| # | Gate | Evidence |
|---|------|----------|
| 1 | `git diff --stat master..HEAD` | `scripts/backup_restore.py      \| 276 +++++++++++++++++++++++----`<br>`tests/unit/test_backup_load.py \| 410 +++++++++++++++++++++++++++++++++++++++++`<br>`tests/unit/test_backup_save.py \|  43 +++++`<br>`3 files changed, 695 insertions(+), 34 deletions(-)` |
| 2 | `python -m pytest tests/unit/test_backup_save.py tests/unit/test_backup_load.py -v` | `17 passed in 2.59s` |
| 3 | `python -m mypy --strict src/claude_memory` | `Success: no issues found in 41 source files` |
| 4 | `tox -e contracts` | `SUCCESS: Violations (13) are within baseline (13).` |
| 5 | `python -m bandit -r src/claude_memory -ll` | Verbatim Output:<br>```Test results: >> Issue: [B104:hardcoded_bind_all_interfaces] Possible binding to all interfaces. Severity: Medium Location: src/claude_memory\embedding_server.py:148:26``` (Accepted baseline) |
| 6 | `python -m ruff check src/claude_memory tests scripts` | `All checks passed!` |
| 7 | `git diff --name-only master..HEAD` | ✅ Matches exactly:<br>`scripts/backup_restore.py`<br>`tests/unit/test_backup_load.py`<br>`tests/unit/test_backup_save.py` |
| 8 | Two-commit topology | ✅ Commit A (implementation `4886861`) and Commit B (handoff) |

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
Run started:2026-08-14 16:50:23.689623+00:00

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

Scanned 139 files. Found 13 violations.

By category:
  Bare Pass: 6
  Silent Fallback: 5
  Per-Item Swallow: 2

Report saved to contract_violations_report.md

SUCCESS: Violations (13) are within baseline (13).
  contracts: OK (11.16=setup[10.78]+cmd[0.05,0.33] seconds)
  congratulations :) (11.20 seconds)
```

---

## Multi-seed sweep evidence (Checklist requirement)

The complete PR-1B test suite runs clean and outputs warning-free executions under `-W error` across multi-seed executions.

### Seed 1 Sweep (seed=1)
`python -m pytest tests/unit/test_backup_save.py tests/unit/test_backup_load.py -W error --randomly-seed=1 -v`
```text
Using --randomly-seed=1
rootdir: C:\Users\Asus\.gemini\antigravity\scratch\new_project\claude-memory-mcp
configfile: pyproject.toml
plugins: anyio-4.12.0, hypothesis-6.151.5, asyncio-1.3.0, benchmark-5.2.3, cov-7.0.0, forked-1.6.0, randomly-4.0.1, timeout-2.4.0, xdist-3.8.0, schemathesis-4.9.5, syrupy-5.1.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 17 items

tests/unit/test_backup_save.py::test_neutral_happy_path PASSED           [  5%]
tests/unit/test_backup_save.py::test_evil_download_yields_truncated_file PASSED [ 11%]
tests/unit/test_backup_save.py::test_evil_falkor_corrupt_gzip PASSED     [ 17%]
tests/unit/test_backup_save.py::test_evil_download_fails_cleans_up_snapshot PASSED [ 23%]
tests/unit/test_backup_save.py::test_sad_collection_does_not_exist PASSED [ 29%]
tests/unit/test_backup_save.py::test_neutral_verify_backup_well_formed PASSED [ 35%]
tests/unit/test_backup_save.py::test_evil_falkordb_save_raises PASSED    [ 41%]
tests/unit/test_backup_save.py::test_evil_snapshot_delete_raises_after_good_download PASSED [ 47%]
tests/unit/test_backup_save.py::test_evil_create_snapshot_raises PASSED  [ 52%]
tests/unit/test_backup_load.py::test_evil_both_snapshot_and_legacy_tar PASSED [ 58%]
tests/unit/test_backup_load.py::test_evil_no_qdrant_artifact PASSED      [ 64%]
tests/unit/test_backup_load.py::test_neutral_legacy_tar_restore PASSED   [ 70%]
tests/unit/test_backup_load.py::test_sad_qdrant_not_ready_timeout PASSED [ 76%]
tests/unit/test_backup_load.py::test_evil_snapshot_upload_fails PASSED   [ 82%]
tests/unit/test_backup_load.py::test_sad_empty_sentinel_restore PASSED   [ 88%]
tests/unit/test_backup_load.py::test_evil_legacy_qdrant_untar_fails PASSED [ 94%]
tests/unit/test_backup_load.py::test_neutral_snapshot_happy_path PASSED  [100%]

============================= 17 passed in 2.59s ==============================
```

### Seed 2 Sweep (seed=2)
`python -m pytest tests/unit/test_backup_save.py tests/unit/test_backup_load.py -W error --randomly-seed=2 -v`
```text
Using --randomly-seed=2
rootdir: C:\Users\Asus\.gemini\antigravity\scratch\new_project\claude-memory-mcp
configfile: pyproject.toml
plugins: anyio-4.12.0, hypothesis-6.151.5, asyncio-1.3.0, benchmark-5.2.3, cov-7.0.0, forked-1.6.0, randomly-4.0.1, timeout-2.4.0, xdist-3.8.0, schemathesis-4.9.5, syrupy-5.1.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 17 items

tests/unit/test_backup_save.py::test_evil_falkor_corrupt_gzip PASSED     [  5%]
tests/unit/test_backup_save.py::test_evil_snapshot_delete_raises_after_good_download PASSED [ 11%]
tests/unit/test_backup_save.py::test_evil_download_fails_cleans_up_snapshot PASSED [ 17%]
tests/unit/test_backup_save.py::test_neutral_verify_backup_well_formed PASSED [ 23%]
tests/unit/test_backup_save.py::test_sad_collection_does_not_exist PASSED [ 29%]
tests/unit/test_backup_save.py::test_evil_falkordb_save_raises PASSED    [ 35%]
tests/unit/test_backup_save.py::test_evil_create_snapshot_raises PASSED  [ 41%]
tests/unit/test_backup_save.py::test_evil_download_yields_truncated_file PASSED [ 47%]
tests/unit/test_backup_save.py::test_neutral_happy_path PASSED           [ 52%]
tests/unit/test_backup_load.py::test_evil_legacy_qdrant_untar_fails PASSED [ 58%]
tests/unit/test_backup_load.py::test_evil_snapshot_upload_fails PASSED   [ 64%]
tests/unit/test_backup_load.py::test_neutral_snapshot_happy_path PASSED  [ 70%]
tests/unit/test_backup_load.py::test_sad_empty_sentinel_restore PASSED   [ 76%]
tests/unit/test_backup_load.py::test_evil_no_qdrant_artifact PASSED      [ 82%]
tests/unit/test_backup_load.py::test_evil_both_snapshot_and_legacy_tar PASSED [ 88%]
tests/unit/test_backup_load.py::test_neutral_legacy_tar_restore PASSED   [ 94%]
tests/unit/test_backup_load.py::test_sad_qdrant_not_ready_timeout PASSED [100%]

============================= 17 passed in 2.54s ==============================
```

### Seed 3 Sweep (seed=3)
`python -m pytest tests/unit/test_backup_save.py tests/unit/test_backup_load.py -W error --randomly-seed=3 -v`
```text
Using --randomly-seed=3
rootdir: C:\Users\Asus\.gemini\antigravity\scratch\new_project\claude-memory-mcp
configfile: pyproject.toml
plugins: anyio-4.12.0, hypothesis-6.151.5, asyncio-1.3.0, benchmark-5.2.3, cov-7.0.0, forked-1.6.0, randomly-4.0.1, timeout-2.4.0, xdist-3.8.0, schemathesis-4.9.5, syrupy-5.1.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 17 items

tests/unit/test_backup_load.py::test_neutral_snapshot_happy_path PASSED  [  5%]
tests/unit/test_backup_load.py::test_evil_both_snapshot_and_legacy_tar PASSED [ 11%]
tests/unit/test_backup_load.py::test_sad_qdrant_not_ready_timeout PASSED [ 17%]
tests/unit/test_backup_load.py::test_evil_no_qdrant_artifact PASSED      [ 23%]
tests/unit/test_backup_load.py::test_neutral_legacy_tar_restore PASSED   [ 29%]
tests/unit/test_backup_load.py::test_evil_legacy_qdrant_untar_fails PASSED [ 35%]
tests/unit/test_backup_load.py::test_evil_snapshot_upload_fails PASSED   [ 41%]
tests/unit/test_backup_load.py::test_sad_empty_sentinel_restore PASSED   [ 47%]
tests/unit/test_backup_save.py::test_sad_collection_does_not_exist PASSED [ 52%]
tests/unit/test_backup_save.py::test_evil_snapshot_delete_raises_after_good_download PASSED [ 58%]
tests/unit/test_backup_save.py::test_evil_download_yields_truncated_file PASSED [ 64%]
tests/unit/test_backup_save.py::test_neutral_verify_backup_well_formed PASSED [ 70%]
tests/unit/test_backup_save.py::test_evil_download_fails_cleans_up_snapshot PASSED [ 76%]
tests/unit/test_backup_save.py::test_evil_falkordb_save_raises PASSED    [ 82%]
tests/unit/test_backup_save.py::test_evil_create_snapshot_raises PASSED  [ 88%]
tests/unit/test_backup_save.py::test_evil_falkor_corrupt_gzip PASSED     [ 94%]
tests/unit/test_backup_save.py::test_neutral_happy_path PASSED           [100%]

============================= 17 passed in 2.52s ==============================
```

### Seed 4 Sweep (seed=4)
`python -m pytest tests/unit/test_backup_save.py tests/unit/test_backup_load.py -W error --randomly-seed=4 -v`
```text
Using --randomly-seed=4
rootdir: C:\Users\Asus\.gemini\antigravity\scratch\new_project\claude-memory-mcp
configfile: pyproject.toml
plugins: anyio-4.12.0, hypothesis-6.151.5, asyncio-1.3.0, benchmark-5.2.3, cov-7.0.0, forked-1.6.0, randomly-4.0.1, timeout-2.4.0, xdist-3.8.0, schemathesis-4.9.5, syrupy-5.1.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 17 items

tests/unit/test_backup_save.py::test_sad_collection_does_not_exist PASSED [  5%]
tests/unit/test_backup_save.py::test_evil_falkordb_save_raises PASSED    [ 11%]
tests/unit/test_backup_save.py::test_evil_create_snapshot_raises PASSED  [ 17%]
tests/unit/test_backup_save.py::test_evil_falkor_corrupt_gzip PASSED     [ 23%]
tests/unit/test_backup_save.py::test_evil_download_yields_truncated_file PASSED [ 29%]
tests/unit/test_backup_save.py::test_neutral_happy_path PASSED           [ 35%]
tests/unit/test_backup_save.py::test_evil_download_fails_cleans_up_snapshot PASSED [ 41%]
tests/unit/test_backup_save.py::test_neutral_verify_backup_well_formed PASSED [ 47%]
tests/unit/test_backup_save.py::test_evil_snapshot_delete_raises_after_good_download PASSED [ 52%]
tests/unit/test_backup_load.py::test_neutral_snapshot_happy_path PASSED  [ 58%]
tests/unit/test_backup_load.py::test_sad_qdrant_not_ready_timeout PASSED [ 64%]
tests/unit/test_backup_load.py::test_neutral_legacy_tar_restore PASSED   [ 70%]
tests/unit/test_backup_load.py::test_evil_legacy_qdrant_untar_fails PASSED [ 76%]
tests/unit/test_backup_load.py::test_evil_both_snapshot_and_legacy_tar PASSED [ 82%]
tests/unit/test_backup_load.py::test_sad_empty_sentinel_restore PASSED   [ 88%]
tests/unit/test_backup_load.py::test_evil_no_qdrant_artifact PASSED      [ 94%]
tests/unit/test_backup_load.py::test_evil_snapshot_upload_fails PASSED   [100%]

============================= 17 passed in 2.49s ==============================
```
