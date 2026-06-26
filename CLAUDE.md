# Dragon Brain — CLAUDE.md

Drop this file into your project root or reference it from your Claude Code config to teach Claude how to use its persistent memory layer.

## The Harness

**Assertive pushback is non-negotiable. See global `~/.claude/CLAUDE.md` § The Harness — 6 guardrails against yes-man behavior. Do NOT hedge-then-agree. Say NO first, make Tabish argue his case. Devil's advocate on every decision. Name the opportunity cost.**

## Audit Remediation (April–May 2026, Complete 2026-05-09)

4-phase adversarial audit found **83 contract violations across 37 source files**. Fixes shipped in 10 batches B1–B10, all landed. Final state: **violations 64 → 13 (~80% eliminated)**, remainder are documented legitimate fallback paths under quarterly review.

**B10 — Async-Native Repository Migration** (completed in 8 sub-batches B10.A through B10.epic-finalize):
- AsyncMemoryRepository wrapper (`asyncio.to_thread` over FalkorDB sync client; ~75 call sites migrated across CrudMixin, SearchChannels, TemporalMixin, ValidationMixin, AnalysisMixin)
- Cross-store compensation in `crud.py` for create/update/delete entity — Qdrant upsert failure rolls back FalkorDB write to prevent split-brain orphans
- Integration test harness via `testcontainers-python` at `tests/integration/test_db_kill_scenarios.py` — 5 tests using real `container.kill()` to assert SearchError on infra outage
- 1230 unit tests pass under `-W error`, zero RuntimeWarning leakage
- B10.5 (native async via `falkordb.asyncio.FalkorDB`) deferred as future epic — current `asyncio.to_thread` wrapper is a correct intermediate state; FalkorDB v1.4.0 ships native async support, future epic will swap the wrapper out
- B10.F entity-level lock granularity deliberately deferred to a future epic — project-level locks remain in place, appropriate for current scale

### The lie this audit closed

Before B1: `search()` wrapped its entire pipeline in `except Exception: return []`. The MCP `search_memory` tool transformed that into `"No results found."` — a confident string lie indistinguishable from "I genuinely have no memories on this topic." Every degraded memory query was Claude operating on missing context without knowing it. **For two months, Dragon Brain was potentially lying to Claude on every infrastructure failure.**

### The contract that matters now

- **`SearchError`** is raised on infrastructure failure (FalkorDB unreachable, Qdrant down, embedding API failed). Defined in `src/claude_memory/exceptions.py`.
- **Empty list** signals "no results found" only — never infrastructure failure.
- **MCP `search_memory`** returns structured `{"error": "MEMORY_LAYER_DEGRADED", "retry_safe": True}` on infra failure. The string `"No results found."` is reserved for legitimate empty results.

If you hit `MEMORY_LAYER_DEGRADED`, the memory layer is broken — don't assume the user has no memories about the topic. Surface the degradation; offer to retry.

### Other audit-driven invariants

- **Edge writes use `MERGE`, not `CREATE`** — retried `create_relationship` calls don't duplicate edges (B2)
- **Cross-channel status propagates** in search responses — when one of the 6 retrieval channels fails, response carries per-channel health metadata (B3)
- **FTS write failures propagate** to caller receipts — silent index staleness eliminated (B2)
- **Lock manager raises `TimeoutError`** on contention — never silently proceeds without the lock (B5)
- **MCP tools have semantic validation** — bad UUIDs return `{"error": "ENTITY_NOT_FOUND"}`, not silent empty results (B6)

### CI gate

`tox -e contracts` runs `scripts/trace_contracts_dragon.py`. **Baseline = 13** (down from 64 at audit start). Quarterly baseline reduction reviews ratchet toward zero.

**If your commit produces NEW violations, the build fails before merge.** This is the regression guardrail; respect it.

### Test-suite physical enforcement (post-22 lockdown)

After the 14a-22f arc, five layers of physical enforcement guard the test-suite's type-correct mock pattern. Each layer is independently active; together they make the 14-era bug class (wrong-type mocks, suppression sneak-arounds, hand-rolled construction) structurally impossible to reintroduce without explicit intent.

| Layer | Mechanism | Catches |
|---|---|---|
| 1. `branch_write_guard.py` | Pre-commit hook reads `process/issues/N_HARNESS.toml` per-issue denylists | Architect spec edits on builder branches; conftest sneak-arounds; src/ scope creep on test-only PRs |
| 2. `inject_handoff_hash.py` | Pre-commit hook auto-injects implementation Commit A's hash into handoff doc's `**Commit:** <auto>` placeholder | Hand-edited hashes; stale or fabricated commit references in handoffs |
| 3. `verify_handoff_completeness.py` | Pre-commit hook validates handoff files for 4-seed baseline, canonical ruff command, no N/A shortcuts on deterministic gates | Single-seed pre-PR baseline drift (22c/22d/22e R1); `--exclude` flag on ruff (22a/22b R1); N/A shortcuts |
| 4. `trace_contracts_dragon.py` Pattern 12 | AST scanner flags hand-rolled `MemoryService(embedding_service=...)` outside helper + 16 Category D allowlist | Reintroduction of the bug class via new test files or migrations bypassing `make_mock_service()` |
| 5. Existing scanner Patterns 1-11 | Baseline 13 (ratcheting toward zero quarterly) | Original audit-remediation contract violations |

The 16 Category D allowlist files (intentional patterns where helper would change semantics), grouped by architectural reason:

- **Bare-MagicMock stubs (2):** test_router, test_list_orphans
- **Real-dependency tests (2):** test_locking (real LockManager), test_dynamic_validation (real OntologyManager)
- **Mutant-testing factories (3):** test_mutant_dict_crud, test_mutant_dict_services, test_mutant_temporal
- **Lightweight-integration (9):** test_temporal, test_hologram, test_full_workflow, test_analysis_radar, test_entity_lifecycle, test_graph_traversal, test_phase4, test_semantic_radar, test_session

Adding a new test file? Use `make_mock_service()` from `tests/_helpers/mock_factory.py`.
Adding a new file that genuinely needs hand-rolled construction? Add the path to `PATTERN_12_ALLOWLIST` in `scripts/trace_contracts_dragon.py` AND document why in the comment (real-dep usage, mutant testing, integration shape).

### Behavioral integration test harness

`tox -e integration` (set `RUN_INTEGRATION=1`) runs `tests/integration/test_db_kill_scenarios.py` against real `falkordb/falkordb:v4.14.11` and `qdrant/qdrant:v1.16.3` containers via testcontainers. Tests use native `container.kill()` to simulate crashes mid-operation and assert the fail-loud contract holds end-to-end. Local-only by default; CI opt-in.

### Where to dig deeper

- **Audit artifacts:** `audit_phase_1-4_*.md` and `audit_phase_4_synthesis.md` carry the full reasoning, prioritized fix list, and Phase B test plan (in the operator's antigravity brain folder).
- **Architecture:** `docs/ARCHITECTURE.md` (B9 deliverable) — trust boundary diagram + per-boundary contracts.
- **Curriculum:** the patterns this audit applied — shape vs contract, behavioral contract tests, sub-batching discipline, tests-enforcing-bugs — are documented in operator's COMMANDNODE Code Literacy Layer 3.5. Not portable to public consumers; reference for operator-internal sessions only.

## Audit Remediation Round 2 (May 13–17, 2026, v1.2.1)

Round 1 (above) installed the SearchError contract and the `tox -e contracts` CI gate. Round 2 added an **adversarial Auditor seat** to the AI Council trifecta — ChatGPT Codex 5.5 — and proved its value end-to-end.

### What the Auditor caught that Round 1 missed

The previous trifecta (Claude as Architect + Antigravity as Builder) shipped 10 batches of remediation across April-May 2026. Codex's pre-build audit caught three production bugs the prior arc missed entirely:

- **Cypher label injection** at `repository.py:90` — `MERGE (n:{label}:Entity ...)` interpolated unvalidated user input. Fixed in PR-1.
- **Point-in-time `created_at` payload drift** — `search.py:187` filtered Qdrant on a field that `crud.py:136` never wrote. `point_in_time_query` was silently returning wrong answers. Fixed in PR-2.
- **Temporal direction enum drift** — `schema.py:338` accepted `forward`/`backward` but `repository_queries.py:82` only matched `before`/`after`, silently routing unrecognized values to the default "both" branch. Fixed in PR-3.

Plus three architectural improvements the audit drove:
- Observation cross-store compensation symmetric with `create_entity` (PR-4)
- Channel degradation observability through the MCP boundary (PR-5)
- Contract scanner await-detection eliminating 62 false positives (PR-6)

### The trifecta workflow (formalized)

| Seat | Function |
|------|----------|
| **Director** | Strategy, final approval, calibration anchor |
| **Architect** | Specs, audit guidelines, principles docs (this file, ARCHITECTURE.md, Code Literacy curriculum) |
| **Builder** | Per-spec implementation. No editorial authority on principles documents |
| **Auditor** | Adversarial verification per pre-defined criteria |

**Audit-guidelines-before-build:** Auditor's criteria are written by the Architect *before* the Builder starts. The Auditor does not see the build recipe — outcomes, not recipes. Auditing the recipe biases verification toward checkbox-following instead of outcome-achievement.

**Deterministic-tooling-as-fourth-leg:** LLM consensus ≠ correctness. The arc reaffirmed this — `scripts/trace_contracts_dragon.py` (AST contract scanner) caught 62 false-positive violations that LLM eyeballing could not have enumerated. Audit protocol now requires running deterministic tools BEFORE any LLM reasoning.

### New API behavior (v1.2.1)

User-facing changes from this arc:

- `search_memory(include_meta=True)` now includes `metadata.channels` with per-channel health (`vector`, `fts`, `entity`, `temporal`, `relational`, `associative`). See "How to Search" → "Channel Health" below.
- `get_temporal_neighbors(direction=...)` accepts all four spellings as permanent aliases: `before`=`backward`, `after`=`forward`, plus `both`. Previous silent-wrong-answer behavior is fixed.
- `create_memory_type(name=...)` validates the name against `[A-Z][A-Za-z0-9_]{0,63}`. Invalid names raise `ValidationError` at the MCP boundary.
- `point_in_time_query` now actually works as documented (was producing wrong answers pre-v1.2.1).
- `add_observation` now compensates the graph write on Qdrant failure, matching `create_entity`'s pattern. No more graph-only orphan observations on infrastructure failure.

### Where to dig deeper

Full process artifacts public in [`process/`](process/):

- [`process/REMEDIATION_BUILD_SPEC.md`](process/REMEDIATION_BUILD_SPEC.md) — builder-facing spec with per-PR concrete fix steps, test design tables, pre-handoff sanity checklist
- [`process/REMEDIATION_AUDIT_SPEC.md`](process/REMEDIATION_AUDIT_SPEC.md) — auditor-facing spec with protocol, trigger semantics, scope rules
- [`process/PR_1_HANDOFF.md`](process/PR_1_HANDOFF.md) through [`process/PR_6_HANDOFF.md`](process/PR_6_HANDOFF.md) — per-PR completion artifacts with tool outputs and per-criterion evidence

The arc is documented as-shipped, including the 6-round PR-5 saga where the audit caught real defects (test bug masking the actual scenario, scope-creep refactor breaking dashboard/scripts callers, hash hygiene drift) before any could merge.

## What This Is

A persistent memory system for AI agents. Knowledge graph (FalkorDB) + vector search (Qdrant) + MCP server. Any MCP-compatible client can store entities, observations, and relationships — then recall them semantically across sessions. Published on PyPI as `dragon-brain`. **v1.2.1 — 100% recall@5 on LongMemEval (ICLR 2025), no LLM required.**

## Current Architecture

6-channel parallel retrieval pipeline, all channels fire on every query:
- **Dense vector** (Qdrant, BGE-M3 1024d) — semantic similarity
- **FTS5 lexical** (SQLite BM25) — keyword matches embeddings miss
- **Entity-first** (spaCy NER → FalkorDB graph) — entity → MENTIONED_IN → sessions
- **Temporal** (date parser → timeline query) — time-window filtering
- **Relational** (graph traversal) — shared entity connections
- **Associative** (spreading activation) — energy propagation through graph

Fusion: weighted RRF (k=35, PIT percentile normalization). Intent classifier sets per-channel weights (soft routing, no hard gate). Optional cross-encoder reranking (ms-marco-MiniLM, GPU/CPU auto-detect).

## Setup Verification

At the start of every session, verify the memory system is running:

```
docker ps --filter "name=claude-memory"
```

You should see 4 healthy containers: graphdb, qdrant, embeddings, dashboard.

If MCP tools (`search_memory`, `create_entity`, etc.) are not available, the server may need restarting. MCP failures are **silent** — always verify tool availability at session start.

## Updating

```bash
cd claude-memory-mcp
git pull origin master
pip install -e ".[dev]"
```

If Docker images changed: `docker compose pull && docker compose up -d`

## How to Search

### Default (Hybrid Search — Recommended)

```
search_memory(query="your question here")
```

No strategy parameter needed. The default path:
1. Runs vector similarity search (always)
2. Detects query intent (temporal, relational, associative, or semantic)
3. Enriches results with graph signals based on detected intent
4. Merges via Reciprocal Rank Fusion if graph results found entities that vector search missed

### Explicit Strategies (When You Know What You Want)

| Strategy | When to Use | Example |
|----------|-------------|---------|
| `"semantic"` | Pure meaning-based similarity | `search_memory(query="distributed systems", strategy="semantic")` |
| `"temporal"` | Time-based queries | `search_memory(query="last week's work", strategy="temporal")` |
| `"relational"` | Path/connection queries (quote entity names) | `search_memory(query="path between \"Auth\" and \"Database\"", strategy="relational")` |
| `"associative"` | Spreading activation through the graph | `search_memory(query="related to authentication", strategy="associative")` |

### Temporal Window

Temporal queries default to a 7-day lookback. Widen if needed:

```
search_memory(query="recent progress", temporal_window_days=14)
```

Use `include_meta=True` to see if there are more results beyond the window:

```
search_memory(query="recent work", include_meta=True)
```

If the response includes `"temporal_exhausted": true`, widen the window for more history.

### Channel Health (v1.2.1+)

When you pass `include_meta=True`, the response also includes per-channel health for all 6 retrieval channels:

```python
result = search_memory(query="recent work", include_meta=True)
# result["metadata"]["channels"] →
# {
#   "vector":      "healthy",
#   "fts":         "failed",      # FTS DB unreachable
#   "entity":      "healthy",
#   "temporal":    "healthy",
#   "relational":  "healthy",
#   "associative": "degraded",
# }
```

Channel statuses:
- `"healthy"` — channel returned results normally
- `"degraded"` — channel completed but with reduced quality (timeout fallback, partial result, etc.)
- `"failed"` — channel infrastructure unavailable (FTS DB down, Qdrant unreachable, embedding service crashed, etc.)

**Why it matters:** if FTS is down, your search returns only vector results — partial coverage that may miss keyword matches. The metadata tells you when to retry vs when to trust the result set. Pre-v1.2.1 this signal was computed internally but discarded before reaching callers (a shared-state TOCTOU bug also lurked there — fixed by per-call return shape).

### Understanding Results

Each result includes:

| Field | Meaning |
|-------|---------|
| `score` | Primary ranking score (cosine similarity, RRF composite, or activation composite) |
| `retrieval_strategy` | What generated this result: `"semantic"`, `"hybrid"`, `"temporal"`, `"relational"`, `"associative"` |
| `vector_score` | Raw cosine similarity from Qdrant. `null` if entity had no vector match |
| `recency_score` | 0-1 exponential decay. 1.0 = just created, 0.5 = ~7 days old |
| `activation_score` | Spreading activation energy (associative results only) |
| `path_distance` | Graph hops from query anchor (relational results only) |
| `salience_score` | Entity importance/frequency score |

**Key insight:** If `score` is 0.0, check `retrieval_strategy` — it tells you why. A temporal-only result with no vector embedding will legitimately have `score=0.0` and `vector_score=null`.

## How to Store Memories

### Entities (Things)

```
create_entity(name="Project Alpha", node_type="Entity", project_id="my-project")
```

Common node types: `Entity`, `Concept`, `Session`, `Breakthrough`, `Tool`, `Decision`, `Bottle`, `Analogy`, `Issue`, `Project`, `Procedure`, `Person`

**Custom memory type name validation (v1.2.1+):** Custom types created via `create_memory_type(name=...)` are validated against `[A-Z][A-Za-z0-9_]{0,63}` — must start with an uppercase letter, alphanumeric + underscore only, max 64 chars. Invalid names raise `ValidationError` at the MCP boundary. Closes a Cypher injection / graph-corruption-from-typo vector (PR-1).

### Observations (Facts About Things)

```
add_observation(entity_id="<uuid>", content="This project uses a microservices architecture")
```

Observations are automatically embedded and indexed for semantic search. **Adding an observation also re-embeds the parent entity** — entity vectors include observation content for richer semantic matching (not just name/type/description).

### Relationships (Connections)

```
create_relationship(
    from_entity="<uuid>",
    to_entity="<uuid>",
    relationship_type="DEPENDS_ON"
)
```

Common edge types: `RELATED_TO`, `ENABLES`, `IMPLEMENTS`, `DEPENDS_ON`, `PRECEDED_BY`, `PART_OF`, `EVOLVED_FROM`, `SUPERSEDES`

**Wiring rule:** Every entity should have at least one relationship to another entity. Entities connected only to their observations are "near-orphans" — findable via search but invisible to graph traversal.

## How to Explore the Graph

| Tool | Purpose |
|------|---------|
| `get_neighbors(entity_id, depth=1)` | Find connected entities within N hops |
| `traverse_path(from_id, to_id)` | Shortest path between two entities |
| `get_evolution(entity_id)` | Chronological history of an entity's observations |
| `find_cross_domain_patterns(entity_id)` | Non-obvious connections across domains |
| `get_hologram(query, depth=1)` | Rich subgraph visualization around a topic |

## Semantic Radar — Relationship Discovery

Discovers potential relationships by comparing vector similarity against graph distance. **Advisory only — never auto-commits edges.** Use these tools to find missing connections in the graph.

### Entity-Level Radar

```
semantic_radar(entity_id="<uuid>", limit=10, similarity_threshold=0.6)
```

For a single entity, finds semantically similar entities that are poorly connected or disconnected in the graph. Returns scored suggestions with:
- `cosine_similarity` — vector similarity score
- `graph_distance` — shortest path length (`null` = disconnected)
- `radar_score` — composite score: `cosine_sim * ln(1 + graph_distance)`. Higher = bigger gap worth bridging
- `suggested_relationship` — heuristic EdgeType (e.g., `ANALOGOUS_TO`, `BRIDGES_TO`, `ENABLES`)
- `reasoning` — human-readable explanation

Entities already directly connected (graph distance ≤ 1) are filtered out automatically.

### Batch Project Scanner

```
find_semantic_opportunities(project_id="my-project", limit=20, similarity_threshold=0.65, min_graph_distance=3)
```

Scans all entities in a project (capped at 200) to find the highest-value bridge opportunities. Deduplicates bidirectional pairs. Use this for periodic graph hygiene — "show me all missing connections."

`min_graph_distance=3` is intentionally more aggressive than entity-level radar's `≤ 1` filter — batch scanning surfaces only significant gaps.

### Weak Connection Analysis (Advanced)

After running `search_associative()`, you can pipe the activation map and vector scores into `detect_weak_connections()` (on the `ActivationEngine`) to identify:
- **Bridge opportunities** — vector-similar but graph-unreachable entities
- **Questionable edges** — graph-connected but semantically dissimilar entities

This is a standalone utility, not an MCP tool. Use it programmatically when doing deep graph analysis.

## Benchmark

Dragon Brain scores **100% recall@5** on LongMemEval (ICLR 2025), the industry-standard AI memory benchmark. 500 questions, 6 categories, no LLM required.

Full results: [benchmarks/longmemeval/RESULTS.md](benchmarks/longmemeval/RESULTS.md)

### Environment Variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `RADAR_MAX_DISTANCE_FACTOR` | `5.0` | Heuristic distance for disconnected entities (scored as `ln(1 + factor*10)`) |
| `RADAR_CONCURRENCY` | `10` | Max concurrent graph queries in batch scanner |

## How to Track Time

| Tool | Purpose |
|------|---------|
| `query_timeline(start, end)` | Entities within a time window |
| `get_temporal_neighbors(entity_id)` | Entities connected by temporal edges |
| `point_in_time_query(query, as_of)` | "What did I know as of this date?" |
| `diff_knowledge_state(as_of_start, as_of_end)` | "What changed between two dates?" — added/removed/evolved entities and relationships |
| `start_session(project_id, focus)` | Begin a tracked session |
| `end_session(session_id, summary)` | Close a session with outcomes |

### Direction Aliases (v1.2.1+)

`get_temporal_neighbors(direction=...)` accepts four spellings as permanent semantic equivalents:

- `"before"` = `"backward"` — entities connected by incoming temporal edges (the past)
- `"after"` = `"forward"` — entities connected by outgoing temporal edges (the future)
- `"both"` — union (default)

Pre-v1.2.1, only `before` / `after` / `both` produced correct results; `forward` / `backward` silently fell through to the default "both" branch — a silent wrong-answer bug fixed in PR-3. Both naming conventions now first-class.

### Time Diff

```
diff_knowledge_state(
    as_of_start="2026-03-01T00:00:00Z",
    as_of_end="2026-04-01T00:00:00Z",
    project_id="dragon-brain"
)
```

Shows what changed in your knowledge graph between two points — added/removed/evolved entities, new/removed relationships, and supersessions. Use for monthly reviews, sprint retrospectives, or "what did I learn last week?" Add `include_observations=True` for per-entity observation diffs (verbose).

## Health & Diagnostics

```
graph_health()          # Node/edge counts, orphans, density
system_diagnostics()    # Infrastructure status, embedding health
```

If `orphan_count > 0`, investigate before deleting — orphans may carry real data.

### Orphan Management

```
list_orphans(limit=50)   # See all orphan nodes for triage
```

### Drift Detection

```
search_stats()           # Rolling-window search behaviour stats (DRIFT-002)
```

Use `search_stats()` to monitor retrieval strategy distribution, score percentiles, and latency trends. Useful for detecting when something has drifted.

## Session Best Practices

1. **Start:** Verify containers are healthy. Run `search_memory(query="recent work")` to pick up context.
2. **During:** Log important learnings to the graph as you go. Autocompact can clear context without warning.
3. **End:** Create entities for key decisions/learnings. Update relationships. Check `graph_health()`.

## Common Pitfalls

- **MCP failures are silent.** If `search_memory` isn't available, the server may have crashed. Check Docker.
- **Don't pass `strategy="auto"`.** It's deprecated. Just omit the strategy parameter for hybrid search.
- **Observations need entities.** You can't create a free-floating observation — it must be attached to an entity.
- **Graph name is `claude_memory`**, not `dragon_brain` or anything else. If querying FalkorDB directly, use this name.
- **Subagents can't use MCP tools.** Never delegate memory operations to background agents — they don't have MCP access.
