# INFRA-1 — Audit Spec (Truthful Backups)

**Issue:** INFRA-1, PRs 1A (`infra-1/backup-snapshot-capture`) and 1B (`infra-1/restore-dual-format`)
**Auditor:** ChatGPT Codex
**Builder spec:** `process/issues/INFRA_1_BUILD_SPEC.md` — **do NOT read.**
**Origin:** your own 2026-08-14 infrastructure audit, finding #4 (live-file Qdrant backup). The Architect verified the finding against the live stack and cut this remediation. You audit outcomes, not the recipe.

---

## ⚠️ LIVE-DATA SAFETY RULE (read before anything else)

The default Docker stack on this machine holds the **production Dragon Brain graph**.

- **NEVER run `python scripts/backup_restore.py load ...` against the default stack.** Restore verification happens via unit tests plus the throwaway-instance round-trip in step (4) below.
- Step (3) briefly stops the live `qdrant` container to prove fail-loud behavior. Restart it immediately after, and confirm all 4 containers are healthy before ending the audit.

## Canonical pass/fail

```bash
# (1) Acceptance tests pass (PR-1A file on 1A audit, both files on 1B audit)
python -m pytest tests/unit/test_backup_save.py -v          # ≥ 8 passing
python -m pytest tests/unit/test_backup_load.py -v          # ≥ 7 passing (PR-1B only)

# (2) LIVE BACKUP TEST — run save against the running stack
python scripts/backup_restore.py save --tag audit_infra1
echo "exit: $?"                                              # must be 0
ls backups/audit_infra1/
# Must contain: falkor_data.tar.gz + qdrant_data.snapshot
# Must NOT contain: qdrant_data.tar.gz
python -c "import tarfile; tarfile.open('backups/audit_infra1/qdrant_data.snapshot'); tarfile.open('backups/audit_infra1/falkor_data.tar.gz'); print('PASS: both artifacts tar-valid')"

# (3) FAIL-LOUD TEST — save must fail nonzero with Qdrant down
docker stop claude-memory-mcp-qdrant-1
python scripts/backup_restore.py save --tag audit_infra1_down
echo "exit: $?"                                              # must be NONZERO
docker start claude-memory-mcp-qdrant-1
# Confirm: no backups/audit_infra1_down/qdrant_data.snapshot claiming success,
# and stderr/stdout clearly names the Qdrant failure.

# (4) ROUND-TRIP TEST (PR-1B only) — recover into a THROWAWAY Qdrant, never the live one
docker run -d --name audit_qdrant -p 127.0.0.1:6340:6333 qdrant/qdrant:v1.16.3
# Wait until http://127.0.0.1:6340/readyz returns 200, then upload the step-(2)
# snapshot via POST /collections/memory_embeddings/snapshots/upload?priority=snapshot
# (multipart file upload). Then compare point counts:
#   GET 127.0.0.1:6340 collection memory_embeddings points_count
#   GET 127.0.0.1:6333 collection memory_embeddings points_count
# Counts must match (allow small delta only if live writes occurred during audit — note it).
docker rm -f audit_qdrant

# (5) Cleanup + stack health
rm -rf backups/audit_infra1 backups/audit_infra1_down
docker ps --filter "name=claude-memory" --format "{{.Names}}\t{{.Status}}"   # 4x healthy
```

**Required outcome:** all steps succeed as annotated. Any deviation = **FAIL**.

## Per-criterion verification

### (a) No live-file copy of Qdrant storage anywhere in the backup path

```bash
grep -n "qdrant" scripts/backup_restore.py
```

Inspect every hit. In `backup()` and its helpers there must be **no** `tar` invocation, `shutil` copy, or `docker run` volume mount that reads `/qdrant/storage` or `/mnt/qdrant_data`. A qdrant-volume tar/untar may appear **only** inside the restore legacy-format branch. Any backup-side fallback that tars live storage on snapshot failure = **FAIL** (silent-degradation reintroduction).

### (b) Snapshot lifecycle hygiene

From step (2): after a successful save, the server-side snapshot must have been deleted —

```bash
curl -s http://127.0.0.1:6333/collections/memory_embeddings/snapshots
```

must not accumulate `audit_infra1`-era snapshots. Leaked snapshots with only a `[WARN]` printed are acceptable **only** when deletion genuinely failed; on the happy path the list must be clean.

### (c) FalkorDB flush is load-bearing

Code inspection + test evidence: a FalkorDB `SAVE` failure must abort the backup with nonzero exit — no "[WARN] ... Proceeding anyway" pattern may remain in `_trigger_persistence` or its caller.

### (d) Verification has teeth

Test evidence must show: corrupt/truncated gzip → verify fails → backup exits nonzero. Size-only checking (the pre-INFRA-1 state) = **FAIL**.

### (e) Scheduler truthfulness

```bash
grep -n "returncode\|status" scripts/scheduled_backup.py | head -20
```

A nonzero exit from `backup_restore.py save` must produce `status: "error"` (or equivalent non-success) in `last_run_status.json`. Verify by inspection; if the builder claims it already worked pre-arc, confirm that claim in code.

### (f) Fresh-install sad path is explicit

Test evidence: absent collection → sentinel artifact distinguishing "legitimately empty" from "missing = corrupt", backup succeeds with a printed note. A backup dir with NO Qdrant artifact of any kind must never verify as OK.

### (g) Restore pre-flight and dual-format (PR-1B)

Test evidence must cover: (i) missing Qdrant artifact → restore refuses BEFORE stopping containers; (ii) legacy `qdrant_data.tar.gz` restores via the unchanged wipe+untar path; (iii) snapshot format recovers via upload API with a readiness wait; (iv) recover failure → nonzero exit, loud error. CLI signatures `save --tag` / `load <tag> --force` unchanged (dashboard `src/dashboard/app.py:246` and the scheduler depend on them).

### (h) Test design discipline

Each behavior carries 3-evil-1-sad-1-neutral coverage (per house methodology). Check test names/asserts, not just counts. Tests must be self-contained (no conftest edits — denied by harness) and pass under `-W error`.

### (i) Scope discipline

```bash
git diff --name-only master..HEAD
```

PR-1A: `scripts/backup_restore.py`, `tests/unit/test_backup_save.py`, `process/PR_INFRA_1A_HANDOFF.md`, plus `scripts/scheduled_backup.py` ONLY if (e) required a fix.
PR-1B: `scripts/backup_restore.py`, `tests/unit/test_backup_load.py`, `process/PR_INFRA_1B_HANDOFF.md`.
Anything else — `src/claude_memory/*`, `src/dashboard/*`, conftest, `pyproject.toml` (new deps), `docker-compose.yml`, `Dockerfile`, any `*_SPEC.md` — = **FAIL**.

### (j) Standard gates

- `tox -e contracts` — baseline 13 holds
- `python -m ruff check src/claude_memory tests scripts` — canonical, no `--exclude`
- `python -m mypy --strict src/claude_memory` — clean
- `python -m bandit -r src/claude_memory -ll` — only accepted B104
- 4-seed pytest baseline in handoff; two-commit topology; handoff `**Commit:**` equals `git rev-parse HEAD~1`

## Discoveries

After per-criterion checks, sweep for adjacent silent-failure patterns in the touched surface:

```bash
grep -n "Proceeding anyway\|check=False" scripts/backup_restore.py scripts/scheduled_backup.py
```

`check=False` on subprocess calls is acceptable only where the return code is explicitly examined afterward; flag any fire-and-forget remnant as a Discovery (not necessarily FAIL — judgment call, report it).

## Output format

Standard. Lead with verdict. Report any live-stack side effects (step 3 downtime window, leftover audit containers/volumes/backup dirs) and confirm end-state: 4 containers healthy, no audit artifacts left behind.
