# INFRA-1 — Truthful Backups (Build Spec)

**Origin:** 2026-08-14 infrastructure audit (Codex, Sol 5.6) — finding #4 of 8, the one of eight that survived Architect verification against the live stack.
**Branch family:** `infra-1/*` — PR-1A: `infra-1/backup-snapshot-capture`, PR-1B: `infra-1/restore-dual-format` (each from current master HEAD; 1B rebases on merged 1A).
**Pattern:** scripts-only production fix. NO `src/claude_memory` changes, NO `src/dashboard` changes, NO compose/Dockerfile changes.
**Harness:** `process/issues/INFRA_1_HARNESS.toml` (branch_write_guard denylist). If the guard does not pick up the `infra-1/*` family, ESCALATE to Architect — do not disable or work around the hook.

---

## The defect (verified against live system, 2026-08-14)

`scripts/backup_restore.py` runs daily via Windows Task Scheduler (`scheduled_backup.py`) and on demand from the dashboard (`src/dashboard/app.py:246`, in-container). Four verified problems:

1. **Qdrant is backed up by tarring its live RocksDB storage** (`backup_restore.py:71-106`) with zero quiesce. `_trigger_persistence()` only flushes FalkorDB. A tar of a running Qdrant's storage directory is not guaranteed restorable. Every existing Qdrant backup is a coin flip.
2. **`_verify_backup` only checks file size ≥ 10KB** (`backup_restore.py:136-153`). A corrupt/truncated archive passes "verification."
3. **`backup()` always exits 0.** Sub-steps print `[FAIL]` and the process still exits clean, so `scheduled_backup.py`'s `last_run_status.json` reports success on failed backups.
4. **FalkorDB `SAVE` failure warns-and-proceeds** (`backup_restore.py:132-133`), silently tarring a stale RDB. Same silent-degradation class the SearchError contract eliminated from search.

## Architect-locked design decisions (do not relitigate; escalate if they break)

- **D1 — Qdrant capture uses the snapshot API, uniformly in both execution modes.** Collection snapshot via sync `QdrantClient` (already a dependency; the async client stays out of this script), downloaded over HTTP (`httpx`, already a dependency) into the backup dir as `qdrant_data.snapshot`. Rationale: the dashboard container has no docker socket, so quiesce-by-container-stop cannot work in-container; the snapshot API is the one mechanism valid in both modes, needs no downtime, and is Qdrant's supported consistency mechanism.
- **D2 — The backup path must contain NO live-file copy of Qdrant storage.** No `tar` of `/qdrant/storage`, no tar of `/mnt/qdrant_data`. The in-container ro-mount tar branch for Qdrant is deleted, not deprecated. (FalkorDB's post-`SAVE` tar stays — RDB files are replaced by atomic rename; a post-SAVE tar is consistent.)
- **D3 — New artifact format, dual-format restore.** New backups contain `falkor_data.tar.gz` + `qdrant_data.snapshot` (or `qdrant_data.EMPTY` sentinel — see D5). Restore must also still handle legacy `qdrant_data.tar.gz` backups unchanged. If a backup dir somehow contains both, prefer `.snapshot` and print a warning.
- **D4 — Fail loud, exit nonzero.** `backup()` and `restore()` return `bool`; `__main__` exits 1 on `False`. FalkorDB `SAVE` failure aborts the backup (no more warn-and-proceed). Any Qdrant snapshot failure aborts the backup — there is NO fallback to live-file copy under any error condition.
- **D5 — Fresh-install sad path is explicit, not silent.** If the `memory_embeddings` collection does not exist, backup writes a zero-byte `qdrant_data.EMPTY` sentinel and succeeds with a printed note. Restore of an `.EMPTY` backup skips Qdrant recovery (the service recreates the collection on next boot). This distinguishes "legitimately empty" from "missing file = broken backup."
- **D6 — No new dependencies, no docker-socket mounts, CLI unchanged.** `save --tag` / `load <tag> --force` signatures stay as-is (dashboard and scheduler depend on them). Config via existing env vars: `QDRANT_HOST` (default `localhost`), `QDRANT_PORT` (default `6333`), new `QDRANT_COLLECTION` (default `memory_embeddings`).

---

## PR-1A — `infra-1/backup-snapshot-capture`

### Files in scope

- **Modify:** `scripts/backup_restore.py` — `backup()`, `_trigger_persistence()`, `_verify_backup()`, `__main__` exit codes; new `_snapshot_qdrant()`
- **Modify (only if needed):** `scripts/scheduled_backup.py` — confirm a nonzero exit from `backup_restore.py save` is surfaced as `status: "error"` in `last_run_status.json`; if it already is, do not touch the file
- **New:** `tests/unit/test_backup_save.py`
- **New:** `process/PR_INFRA_1A_HANDOFF.md`

### Behavior spec

**B1 — Qdrant snapshot capture** (`_snapshot_qdrant(target_dir) -> bool`):
1. Sync `QdrantClient(host, port, timeout=120)` from env per D6.
2. If collection absent → write `qdrant_data.EMPTY` sentinel, print note, return True (D5).
3. `create_snapshot(collection_name=..., wait=True)`.
4. Download `GET http://{host}:{port}/collections/{collection}/snapshots/{snapshot.name}` via `httpx` streaming to `{target_dir}/qdrant_data.snapshot`.
5. Delete the server-side snapshot after successful download. Deletion failure → print `[WARN]`, still return True (backup artifact is already safe; leaked server-side snapshots are a hygiene issue, not a data-loss issue).
6. Any failure in steps 1-4 → print `[FAIL]` with the reason, return False. No fallback path (D2/D4).

**B2 — FalkorDB flush becomes load-bearing:** `_trigger_persistence()` returns `bool`; on failure `backup()` aborts before archiving anything and returns False (D4).

**B3 — Verification with teeth** (`_verify_backup(target_dir) -> bool`):
- `falkor_data.tar.gz`: must open with `tarfile.open` and contain ≥ 1 member (catches gzip corruption, truncation, and empty-dir tars). Size check stays as a secondary warning.
- Qdrant: exactly one of `qdrant_data.snapshot` / `qdrant_data.EMPTY` must exist. `.snapshot` must open with `tarfile.open` (Qdrant snapshots are tar archives) and be ≥ 10KB.
- Any check failing → return False, which fails the backup (D4).

**B4 — Exit-code contract:** `save` happy path exits 0; any of B1/B2/B3 failing exits 1. `scheduled_backup.py` must surface that as an error status (verify; patch minimally only if it doesn't).

### Test design (3-evil-1-sad-1-neutral per behavior) — `tests/unit/test_backup_save.py`

Mock `qdrant_client.QdrantClient`, `httpx`, `redis`, and `subprocess.run`; use `tmp_path`. No real containers. Self-contained file — conftest changes are denied by harness.

| # | Kind | Scenario | Must assert |
|---|------|----------|-------------|
| 1 | evil | `create_snapshot` raises (Qdrant down) | `backup()` False; no `qdrant_data.tar.gz` created; no tar subprocess invoked against a qdrant volume; exit 1 via `__main__` path |
| 2 | evil | download yields truncated file (< 10KB / not a tar) | verification fails; `backup()` False |
| 3 | evil | FalkorDB `SAVE` raises | `backup()` False BEFORE any archive step runs (no falkor tar attempted) |
| 4 | evil | server-side snapshot delete raises after good download | `backup()` True; `[WARN]` printed; `.snapshot` present and valid |
| 5 | sad | collection does not exist | `backup()` True; `qdrant_data.EMPTY` written; no `.snapshot`; note printed |
| 6 | neutral | happy path | `backup()` True; `.snapshot` + `falkor_data.tar.gz` present; verify passes; server-side snapshot deleted; exit 0 |
| 7 | evil | `falkor_data.tar.gz` is corrupt gzip (crafted fixture) | `_verify_backup` False |
| 8 | neutral | `_verify_backup` on a well-formed backup dir (crafted valid tars) | True |

≥ 8 tests, all passing under `-W error`.

---

## PR-1B — `infra-1/restore-dual-format`

**AMENDED 2026-08-14 after PR-1A audit PASS.** Two changes driven by Codex's non-blocking discoveries: (1) new behavior B7 closes the server-side snapshot leak window in `_snapshot_qdrant` (a download failure between `create_snapshot` and `delete_snapshot` skipped cleanup — spec gap in B1, not a builder defect); (2) the original test row 5 wording "byte-identical to current behavior" contradicted B6's exit-code contract for the legacy restore path and is corrected below — legacy restore runs the SAME docker commands but must now examine their return codes.

### Files in scope

- **Modify:** `scripts/backup_restore.py` — `restore()` + the B7 micro-fix in `_snapshot_qdrant`
- **New:** `tests/unit/test_backup_load.py`
- **Modify:** `tests/unit/test_backup_save.py` — one new evil test for B7 only
- **New:** `process/PR_INFRA_1B_HANDOFF.md`

### Behavior spec

**B5 — Dual-format restore sequence:**
1. Validate the backup dir BEFORE stopping anything: `falkor_data.tar.gz` must exist AND exactly one of {`qdrant_data.snapshot`, `qdrant_data.EMPTY`, legacy `qdrant_data.tar.gz`} must exist. Neither → `[FAIL]`, return False, containers untouched (no partial restore). Both `.snapshot` and legacy tar → prefer `.snapshot`, print `[WARN]`.
2. Stop containers (existing behavior).
3. FalkorDB volume restore — unchanged legacy path.
4. Legacy format: Qdrant volume wipe+untar (existing behavior), then `up -d`.
5. Snapshot format: `up -d` first, poll Qdrant readiness (existing env host/port, timeout 60s), then recover via multipart upload: `POST /collections/{collection}/snapshots/upload?priority=snapshot` (httpx). This recreates the collection even if absent.
6. `.EMPTY` format: `up -d`, print note, skip recovery.
7. Post-restore verification: unless `.EMPTY`, `get_collections()` must list the collection; print its point count. Failure → `[FAIL]`, return False, exit 1 (containers left running — state is visible, not hidden).

**B6 — Exit-code contract:** `load` exits 0 only if every step above succeeded. This INCLUDES the legacy-format docker subprocesses (falkor untar, qdrant wipe+untar, compose stop/up): same commands as today, but their return codes must be examined — a nonzero `docker run` can no longer be followed by `[OK]`. (`check=False` may remain only where the return code is explicitly inspected afterward.)

**B7 — Snapshot cleanup on download failure** (in `_snapshot_qdrant`, from PR-1A audit discovery): if the download step fails after `create_snapshot` succeeded, attempt `delete_snapshot` before returning False (try/finally or equivalent). Cleanup failure on this path → `[WARN]`, still return False. The backup outcome is unchanged (loud failure); this only prevents leaking server-side snapshots on the Qdrant instance.

### Test design — `tests/unit/test_backup_load.py`

Same mocking rules as PR-1A.

| # | Kind | Scenario | Must assert |
|---|------|----------|-------------|
| 1 | evil | backup dir has no Qdrant artifact at all | False; `docker-compose stop` NEVER invoked (pre-flight catches it) |
| 2 | evil | snapshot upload-recover returns HTTP error | False; exit 1; error printed |
| 3 | evil | both `.snapshot` and legacy tar present | `.snapshot` path taken; `[WARN]` printed |
| 4 | sad | Qdrant not ready within timeout after `up -d` | False; loud message naming the container |
| 5 | neutral | legacy `qdrant_data.tar.gz` backup | legacy wipe+untar path invoked with the same docker commands as current behavior, return codes examined per B6; True |
| 6 | neutral | `.snapshot` happy path | ordered: stop → falkor untar → up → readiness poll → upload → collection verified; True |
| 7 | sad | `.EMPTY` backup | Qdrant recovery skipped, note printed, True |
| 8 | evil | legacy path: qdrant untar subprocess returns nonzero | `restore()` False; exit 1; no `[OK]` printed for the failed step |
| 9 | evil (B7, in `test_backup_save.py`) | download raises after successful `create_snapshot` | `backup()` False AND `delete_snapshot` was attempted with the created snapshot's name |

≥ 9 tests total across the two files, all passing under `-W error`.

---

## Gates (both PRs)

- `python -m pytest tests/unit/ -x -q` under 4-seed baseline (0, 1, 42, 1337) per master spec — all pass, zero warnings under `-W error`
- `tox -e contracts` — baseline holds at 13
- `python -m ruff check src/claude_memory tests scripts` — canonical command, NO `--exclude`
- `python -m mypy --strict src/claude_memory` — clean (scripts are outside strict scope; do not add them)
- `python -m bandit -r src/claude_memory -ll` — only accepted B104
- Two-commit topology: implementation commit + handoff commit; `**Commit:** <auto>` placeholder for `inject_handoff_hash`
- Handoff files: `process/PR_INFRA_1A_HANDOFF.md` / `process/PR_INFRA_1B_HANDOFF.md`. If `verify_handoff_completeness.py`'s file pattern does not match these names, ESCALATE to Architect for a hook-pattern update on master — do not rename around the hook, do not skip it.

## Escalation rule

If any locked decision (D1-D6) proves wrong at runtime — e.g., the installed `qdrant-client` lacks a needed sync method, or the snapshot endpoint shape differs on v1.16.3 — STOP and escalate to the Architect with the exact error. Do not silently redesign, do not patch this spec (denied by harness), do not add dependencies.
