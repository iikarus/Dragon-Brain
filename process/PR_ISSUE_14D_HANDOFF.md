# Issue #14d Handoff — test_embedding_filter.py Async Mock Cleanup

**Commit:** `5728ed1b0ba6801e764bcd3727645c1ec7978a95`
**Branch:** issue-14d/test-embedding-filter-hologram-cleanup
**Issue:** [#14d / parent #14](https://github.com/iikarus/Dragon-Brain/issues/14)

## Discovery findings

Single-file target (177 lines, 5 tests). File-level baseline: 3 sentinel hits. Full-suite baseline: 6 hits (this was the sole remaining emitter after 14a/14b/14c).

### Fix 1 — `_fire_salience_update` not mocked (L58)

- **Root cause:** Same as 14a/14b — `asyncio.create_task(repo.increment_salience(...))` spawns orphan coroutines.
- **Fix:** `service._fire_salience_update = MagicMock()` in fixture.
- **Note:** `test_happy_search_results_have_no_embedding_field` already had its own inline `mock_service._fire_salience_update = MagicMock()` at L142; the fixture-level mock makes this redundant but harmless.

### Fix 2 — Per-file `_drain_orphan_coroutines` autouse fixture (L21-37)

- **Rationale:** Same as all prior sub-chunks — drains `AsyncMockMixin._execute_mock_call` coroutines within test boundaries via `gc.collect()`.

### Assertion trap (L63-76) — architect-injected

`test_meta_fixture_topology_required` validates `mock_service.repo` and `mock_service.vector_store` are `AsyncMock`.

## Pre-handoff checklist

| # | Gate | Evidence |
|---|------|----------|
| 1 | `git diff --stat` | `tests/unit/test_embedding_filter.py \| 38 +++++++++++++++++++++++++++++++++++++` (1 file, +38/-0) |
| 2 | `python -m pytest tests/unit/test_embedding_filter.py -q` | `6 passed in 15.65s` |
| 3 | `python -m pytest tests/unit/ -q` | `1287 passed in 225.00s` |
| 4 | `python -m mypy --strict src/claude_memory` | `Success: no issues found in 40 source files` |
| 5 | `tox -e contracts` | `SUCCESS: Violations (13) are within baseline (13). congratulations :)` |
| 6 | `python -m bandit -r src/claude_memory -ll` | 1 Medium: B104 `embedding_server.py:148` (accepted baseline) |
| 7 | `python -m ruff check tests/unit/test_embedding_filter.py` | `All checks passed!` |
| 8 | No `src/claude_memory/` changes | ✅ Only `tests/unit/test_embedding_filter.py` modified |
| 9 | Strict-gate acceptance | ✅ ZERO sentinel matches — BOTH file-level AND full-suite (see below) |

## Empirical strict-gate verification

### File-level (criterion 1)
```
$ python -m pytest tests/unit/test_embedding_filter.py -W error::RuntimeWarning -v 2>&1 \
    | grep -E "RuntimeWarning|PytestUnraisableExceptionWarning"
(zero output — PASS)
```
Full result: `6 passed in 15.65s`, sentinel hits: 0.

### Full-suite (criterion 2 — true #14 closure)
```
$ python -m pytest tests/unit/ -W error::RuntimeWarning -q --tb=no 2>&1 \
    | grep -E "RuntimeWarning|PytestUnraisableExceptionWarning"
(zero output — PASS)
```
Full result: `1287 passed in 225.00s`, sentinel hits: 0.

## Definition of done

Both canonical acceptance criteria met. Issue #14 can be closed for real.
