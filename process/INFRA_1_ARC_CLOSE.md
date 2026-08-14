# INFRA-1 Arc Close — Truthful Backups

**Dates:** 2026-08-14 (single day, two PRs)
**Trifecta:** Tabish (Director) · Claude (Architect) · Antigravity (Builder) · ChatGPT Codex (Auditor)
**Result:** Both PRs PASS on first audit. Round-trip proof: 2,270 live points → snapshot → cold-recover on a throwaway instance → 2,270 points, delta 0.

## Origin

Codex ran an 8-recommendation infrastructure audit of the Docker stack. Architect verification against the live system killed 6 of 8 as production-grade defaults miscalibrated for a single-user localhost tool (one would have broken the host-side MCP server outright), confirmed one as benign (FalkorDB's anonymous `/data` volume — verified inert via `CONFIG GET dir`), and confirmed one as real: **Qdrant was backed up by tarring its live storage, daily, with no quiesce** — plus three adjacent defects found during spec drafting: size-only backup verification, `backup()` always exiting 0 (scheduler status file lied), and FalkorDB `SAVE` failure warn-and-proceed.

## What shipped

- **PR-1A** (`38b36c9`): Qdrant capture via snapshot API (uniform across host and in-container modes), FalkorDB flush made load-bearing, tarfile-integrity verification, truthful exit codes.
- **PR-1B** (`4886861`): dual-format restore (new `.snapshot` / `.EMPTY` + legacy tar), pre-flight validation before stopping containers, readiness-wait + upload-recover, legacy subprocess return codes examined, snapshot-leak closure on download failure (B7).
- **Guard patch** (`636cbd8`, Architect, via AG escalation): `branch_write_guard.py` generalized to `<family>-<N>` branch families — and a latent defect fixed: denylist matching was exact-string only, so **directory entries never matched staged files under them; every directory-level denial in prior harnesses was silently inert.**

## Process findings (the arc's real yield)

1. **Auditor calibration:** 1-of-8 audit recommendations survived, but the survivor was the only one guarding against silent data loss. The Auditor seat finds candidates; the Architect verifies against the live system before any becomes work. Codex's #6 (remove published ports) was factually-correct-in-parts yet system-breaking — it inferred topology instead of verifying that the MCP server runs on the host.
2. **Enforcement must be live-fire tested.** Two guard defects (branch pattern too narrow, directory denials inert) existed precisely because the guard had never been synthetically violated. The INFRA-1 audit specs required synthetic violations for the new code; the same discipline retroactively applied to the guard itself found both holes. Corollary of the hook-regex catches from the B10.5 arc.
3. **The harness caught its own author.** Architect attempted to commit spec amendments while the shared checkout sat on the builder branch; branch_write_guard (hours-old patch included) and architect_branch_guard both blocked it. Physical enforcement is role-blind — that is the point.
4. **Spec self-contradiction caught by Auditor discovery:** original PR-1B test row 5 ("byte-identical" legacy restore) conflicted with B6's exit-code contract. Codex's `check=False` discovery surfaced it before AG hit the wall. Amendment passed the oracle-correction test: the original wording would have forced the Builder to do something *worse*.
5. **Builder escalation discipline held:** AG halted on the guard mismatch per spec instead of working around the hook, and pre-verified the D1 client signatures unprompted — retiring the Architect's one flagged uncertainty.

## Residuals

- Pre-INFRA-1 Qdrant backups (live-file tars) remain restorable-in-theory via the legacy path but were never consistency-verified; treat them as best-effort only. First post-arc `save` supersedes them as the trust baseline.
- Image-bloat housecleaning from the same audit (~25GB: stale `claude-memory-mcp-server` image + old db tags, compose-build layer re-share) remains a 5-minute operator task, deliberately not an arc.
