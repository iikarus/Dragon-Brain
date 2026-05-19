"""Tests for the hybrid search pipeline — ADR-007 §10.2.

3-evil/1-sad/1-happy per function + spec §10.2 checklist coverage.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_memory.router import QueryIntent, QueryRouter
from claude_memory.schema import SearchMemoryParams

if TYPE_CHECKING:
    from claude_memory.tools import MemoryService

# ─── Constants ──────────────────────────────────────────────────────

PROJECT_ID = "project-alpha"
MOCK_EMBEDDING = [0.1, 0.2, 0.3]
NOW_ISO = datetime.now(UTC).isoformat()

# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def service():
    """MemoryService with all deps mocked."""
    mock_embedder = MagicMock()
    mock_embedder.encode.return_value = MOCK_EMBEDDING

    with patch("claude_memory.repository.FalkorDB"):
        with patch("claude_memory.lock_manager.redis.Redis"):
            with patch("claude_memory.vector_store.AsyncQdrantClient"):
                from claude_memory.tools import MemoryService

                svc = MemoryService(embedding_service=mock_embedder)

    svc.repo = AsyncMock()
    svc.repo = AsyncMock()
    svc.activation_engine.repo = svc.repo
    svc.vector_store = AsyncMock()
    svc.fts_store = MagicMock()
    svc.fts_store.search = MagicMock(return_value=[])
    svc.router = MagicMock(spec=QueryRouter)
    # Reranker: pass-through (return candidates unchanged)
    svc.reranker = MagicMock()
    svc.reranker.rerank = AsyncMock(side_effect=lambda q, c, **kw: c)
    # Soft routing: all channels fire, so mock default returns for all enrichments
    svc.query_timeline = AsyncMock(return_value=[])
    svc.traverse_path = AsyncMock(return_value=[])
    svc.search_associative = AsyncMock(return_value=[])
    # Activation engine defaults
    svc.activation_engine.activate = MagicMock(return_value={})
    svc.activation_engine.spread = AsyncMock(return_value={})
    # Prevent fire-and-forget tasks from creating orphan coroutines during gc
    svc._fire_salience_update = MagicMock()
    return svc


def _vector_results(*ids: str) -> list[dict[str, Any]]:
    """Build mock vector search results."""
    return [{"_id": eid, "_score": 0.9 - i * 0.1} for i, eid in enumerate(ids)]


def _graph_nodes(*ids: str) -> dict[str, Any]:
    """Build mock subgraph response for depth=0."""
    return {
        "nodes": [
            {
                "id": eid,
                "name": f"Node-{eid}",
                "node_type": "Entity",
                "project_id": PROJECT_ID,
                "salience_score": 0.5,
                "occurred_at": NOW_ISO,
            }
            for eid in ids
        ],
        "edges": [],
    }


# ═══════════════════════════════════════════════════════════════
#  §10.2 Checklist: Hybrid search pipeline
# ═══════════════════════════════════════════════════════════════


class TestHybridSearchPipeline:
    """Tests covering the hybrid default path (strategy=None)."""

    @pytest.mark.asyncio()
    async def test_sad1_default_search_always_hits_vector_store(self, service) -> None:
        """strategy=None always calls vector_store.search."""
        service.vector_store.search.return_value = _vector_results("a")
        service.router.classify.return_value = QueryIntent.SEMANTIC
        service.repo.get_subgraph.return_value = _graph_nodes("a")

        await service.search(SearchMemoryParams(query="test query"))

        service.vector_store.search.assert_called_once()

    @pytest.mark.asyncio()
    async def test_happy_temporal_intent_triggers_graph_enrichment(
        self, service: MemoryService
    ) -> None:
        """TEMPORAL intent triggers query_timeline alongside vector search."""
        service.vector_store.search.return_value = _vector_results("a")
        service.router.classify.return_value = QueryIntent.TEMPORAL
        service.repo.get_subgraph.return_value = _graph_nodes("a")

        with patch.object(service, "query_timeline", new_callable=AsyncMock) as mock_tl:
            mock_tl.return_value = [{"id": "t1", "name": "Temporal-1"}]
            service.repo.get_subgraph.return_value = _graph_nodes("a", "t1")

            _res = await service.search(SearchMemoryParams(query="what happened recently"))

            _ = _res.get("results", []) if isinstance(_res, dict) else _res

        mock_tl.assert_called_once()
        service.vector_store.search.assert_called_once()

    @pytest.mark.asyncio()
    async def test_happy_relational_intent_triggers_path_enrichment(
        self, service: MemoryService
    ) -> None:
        """RELATIONAL intent with quoted entities triggers traverse_path."""
        service.vector_store.search.return_value = _vector_results("a")
        service.router.classify.return_value = QueryIntent.RELATIONAL
        service.repo.get_subgraph.return_value = _graph_nodes("a")

        with patch.object(service, "traverse_path", new_callable=AsyncMock) as mock_tp:
            mock_tp.return_value = [
                {"id": "r1", "name": "Rel-1"},
                {"id": "r2", "name": "Rel-2"},
            ]
            service.repo.get_subgraph.return_value = _graph_nodes("a", "r1", "r2")

            await service.search(SearchMemoryParams(query='path between "auth" and "database"'))

        mock_tp.assert_called_once()

    @pytest.mark.asyncio()
    async def test_happy_associative_intent_triggers_activation(
        self, service: MemoryService
    ) -> None:
        """ASSOCIATIVE intent runs spreading activation with vector seeds."""
        vec_results = _vector_results("a", "b")
        service.vector_store.search.return_value = vec_results
        service.router.classify.return_value = QueryIntent.ASSOCIATIVE

        # Mock the activation engine methods
        service.activation_engine.activate = MagicMock(return_value={"a": 1.0, "b": 1.0})
        service.activation_engine.spread = MagicMock(return_value={"a": 1.0, "b": 0.6, "c": 0.3})
        service.repo.get_subgraph.return_value = _graph_nodes("a", "b", "c")

        _res = await service.search(SearchMemoryParams(query="things related to auth"))

        results = _res.get("results", []) if isinstance(_res, dict) else _res

        # Activation engine was used with the seeds from vector results
        service.activation_engine.activate.assert_called_once_with(["a", "b"])
        assert len(results) > 0

    @pytest.mark.asyncio()
    async def test_sad2_semantic_intent_vectors_dominate(self, service: MemoryService) -> None:
        """SEMANTIC intent: vector channel gets full weight, graph channels get base weight."""
        service.vector_store.search.return_value = _vector_results("a")
        service.router.classify.return_value = QueryIntent.SEMANTIC
        service.repo.get_subgraph.return_value = _graph_nodes("a")

        _res = await service.search(SearchMemoryParams(query="what is Python"))

        results = _res.get("results", []) if isinstance(_res, dict) else _res

        # Under soft routing, all channels fire — vector still dominates
        assert len(results) == 1
        service.vector_store.search.assert_called_once()

    @pytest.mark.asyncio()
    async def test_happy_retrieval_strategy_always_populated(self, service: MemoryService) -> None:
        """retrieval_strategy is never empty/missing on results."""
        service.vector_store.search.return_value = _vector_results("a")
        service.router.classify.return_value = QueryIntent.SEMANTIC
        service.repo.get_subgraph.return_value = _graph_nodes("a")

        _res = await service.search(SearchMemoryParams(query="test query"))

        results = _res.get("results", []) if isinstance(_res, dict) else _res

        for r in results:
            assert r.retrieval_strategy in (
                "semantic",
                "hybrid",
                "temporal",
                "relational",
                "associative",
            )

    @pytest.mark.asyncio()
    async def test_happy_score_never_hardcoded_zero_for_hybrid_with_vector_match(
        self, service: MemoryService
    ) -> None:
        """score > 0 when a vector match exists (the original bug)."""
        service.vector_store.search.return_value = _vector_results("a")
        service.router.classify.return_value = QueryIntent.TEMPORAL
        service.repo.get_subgraph.return_value = _graph_nodes("a")

        with patch.object(service, "query_timeline", new_callable=AsyncMock) as mock_tl:
            mock_tl.return_value = [{"id": "a", "name": "Node-a"}]

            _res = await service.search(SearchMemoryParams(query="recent work"))

            results = _res.get("results", []) if isinstance(_res, dict) else _res

        # The key assertion: score is NOT 0.0 for an entity that has a vector match
        assert len(results) > 0
        for r in results:
            if r.vector_score is not None:
                assert r.score > 0

    @pytest.mark.asyncio()
    async def test_happy_strategy_auto_logs_deprecation(
        self, service: MemoryService, caplog: pytest.LogCaptureFixture
    ) -> None:
        """strategy='auto' logs deprecation warning, runs hybrid path."""
        service.vector_store.search.return_value = _vector_results("a")
        service.router.classify.return_value = QueryIntent.SEMANTIC
        service.repo.get_subgraph.return_value = _graph_nodes("a")

        with caplog.at_level(logging.WARNING):
            _res = await service.search(SearchMemoryParams(query="test", strategy="auto"))
            results = _res.get("results", []) if isinstance(_res, dict) else _res

        assert "deprecated" in caplog.text.lower()
        # Should still return results (ran hybrid path)
        assert len(results) == 1

    @pytest.mark.asyncio()
    async def test_sad3_temporal_window_days_default(self, service: MemoryService) -> None:
        """temporal_window_days=7 default is applied."""
        service.vector_store.search.return_value = _vector_results("a")
        service.router.classify.return_value = QueryIntent.TEMPORAL
        service.repo.get_subgraph.return_value = _graph_nodes("a")

        with patch.object(service, "query_timeline", new_callable=AsyncMock) as mock_tl:
            mock_tl.return_value = [{"id": "a", "name": "Node-a"}]

            await service.search(SearchMemoryParams(query="recent stuff"))

        # Verify the temporal params used 7-day window
        call_args = mock_tl.call_args[0][0]
        window = (call_args.end - call_args.start).days
        assert window == 7

    @pytest.mark.asyncio()
    async def test_happy_temporal_exhausted_flag(self, service: MemoryService) -> None:
        """temporal_exhausted is True when results < limit."""
        service.vector_store.search.return_value = _vector_results("a")
        service.router.classify.return_value = QueryIntent.TEMPORAL
        service.repo.get_subgraph.return_value = _graph_nodes("a", "t1")

        with patch.object(service, "query_timeline", new_callable=AsyncMock) as mock_tl:
            # Return 2 results, but limit is 5 (default) → exhausted
            mock_tl.return_value = [
                {"id": "t1", "name": "Temporal-1"},
            ]

            res = await service.search(SearchMemoryParams(query="recent things", limit=5))

        # 1 result < limit 5 → exhausted
        assert res["metadata"]["temporal_exhausted"] is True


# ═══════════════════════════════════════════════════════════════
#  3e/1s/1h per function: _compute_recency
# ═══════════════════════════════════════════════════════════════


class TestComputeRecency:
    """3e/1s/1h for _compute_recency."""

    def test_happy_recent_timestamp_scores_high(self) -> None:
        """Happy: a timestamp from 1 hour ago scores close to 1.0."""
        from datetime import UTC, datetime, timedelta

        from claude_memory.schema import SearchResult
        from claude_memory.search import SearchMixin

        r = SearchResult(
            id="x",
            name="X",
            node_type="Entity",
            project_id="p",
            score=0.9,
            distance=0.1,
        )
        one_hour_ago = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
        score = SearchMixin._compute_recency(r, occurred_at=one_hour_ago)
        assert score > 0.95  # 1 hour / 7 day half-life → ~0.996

    def test_sad_no_timestamp_returns_default(self) -> None:
        """Sad: no occurred_at → returns existing recency_score (0.0)."""
        from claude_memory.schema import SearchResult
        from claude_memory.search import SearchMixin

        r = SearchResult(
            id="x",
            name="X",
            node_type="Entity",
            project_id="p",
            score=0.9,
            distance=0.1,
        )
        assert SearchMixin._compute_recency(r) == 0.0

    def test_evil_env_var_changes_decay_rate(self) -> None:
        """Evil: RECENCY_HALF_LIFE_DAYS=1 → score at 1 day is ~0.5."""
        from datetime import UTC, datetime, timedelta

        from claude_memory.schema import SearchResult
        from claude_memory.search import SearchMixin

        r = SearchResult(
            id="x",
            name="X",
            node_type="Entity",
            project_id="p",
            score=0.9,
            distance=0.1,
        )
        one_day_ago = (datetime.now(UTC) - timedelta(days=1)).isoformat()
        with patch.dict("os.environ", {"RECENCY_HALF_LIFE_DAYS": "1"}):
            score = SearchMixin._compute_recency(r, occurred_at=one_day_ago)
        assert 0.45 < score < 0.55  # 2^(-1/1) = 0.5

    def test_evil_invalid_env_var_with_timestamp(self) -> None:
        """Evil: invalid env var value raises ValueError when timestamp provided."""
        from datetime import UTC, datetime

        from claude_memory.schema import SearchResult
        from claude_memory.search import SearchMixin

        r = SearchResult(
            id="x",
            name="X",
            node_type="Entity",
            project_id="p",
            score=0.9,
            distance=0.1,
        )
        now = datetime.now(UTC).isoformat()
        with patch.dict("os.environ", {"RECENCY_HALF_LIFE_DAYS": "not_a_number"}):
            with pytest.raises(ValueError):
                SearchMixin._compute_recency(r, occurred_at=now)

    def test_evil_old_timestamp_scores_low(self) -> None:
        """Evil: a timestamp from 30 days ago scores near zero."""
        from datetime import UTC, datetime, timedelta

        from claude_memory.schema import SearchResult
        from claude_memory.search import SearchMixin

        r = SearchResult(
            id="x",
            name="X",
            node_type="Entity",
            project_id="p",
            score=0.9,
            distance=0.1,
        )
        thirty_days_ago = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        score = SearchMixin._compute_recency(r, occurred_at=thirty_days_ago)
        assert score < 0.10  # 2^(-30/7) ≈ 0.015, generous margin


# ═══════════════════════════════════════════════════════════════
#  3e/1s/1h per function: _attach_vector_scores
# ═══════════════════════════════════════════════════════════════


class TestAttachVectorScores:
    """3e/1s/1h for _attach_vector_scores."""

    @pytest.mark.asyncio()
    async def test_happy_attaches_scores(self, service) -> None:
        """Happy: entities found in vector store get real scores."""
        from claude_memory.schema import SearchResult

        results = [
            SearchResult(
                id="a", name="A", node_type="Entity", project_id="p", score=0.0, distance=0.0
            ),
        ]
        service.vector_store.retrieve_by_ids = AsyncMock(return_value={"a": 0.85})

        updated = await service._attach_vector_scores("test query", results)

        assert updated[0].score == pytest.approx(0.85)
        assert updated[0].vector_score == pytest.approx(0.85)

    @pytest.mark.asyncio()
    async def test_sad_empty_results(self, service: MemoryService) -> None:
        """Sad: empty results list returns empty."""
        updated = await service._attach_vector_scores("test", [])
        assert updated == []

    @pytest.mark.asyncio()
    async def test_evil_entity_no_vector_match(self, service) -> None:
        """Evil: entity not found in vector store → score stays 0.0, vector_score=None."""
        from claude_memory.schema import SearchResult

        results = [
            SearchResult(
                id="orphan", name="O", node_type="Entity", project_id="p", score=0.0, distance=0.0
            ),
        ]
        service.vector_store.retrieve_by_ids = AsyncMock(return_value={})

        updated = await service._attach_vector_scores("test", results)

        assert updated[0].score == 0.0
        assert updated[0].vector_score is None

    @pytest.mark.asyncio()
    async def test_evil_vector_store_error_graceful(self, service) -> None:
        """Evil: vector store connection error → scores stay at 0.0, no crash."""
        from claude_memory.schema import SearchResult

        results = [
            SearchResult(
                id="a", name="A", node_type="Entity", project_id="p", score=0.0, distance=0.0
            ),
        ]
        service.vector_store.retrieve_by_ids = AsyncMock(side_effect=ConnectionError("Qdrant down"))

        updated = await service._attach_vector_scores("test", results)

        assert updated[0].score == 0.0  # unchanged

    @pytest.mark.asyncio()
    async def test_evil_retrieval_strategy_set(self, service) -> None:
        """Evil: verify vector_score=None is intentional when entity has no vector."""
        from claude_memory.schema import SearchResult

        results = [
            SearchResult(
                id="graph-only",
                name="G",
                node_type="Entity",
                project_id="p",
                score=0.0,
                distance=0.0,
                retrieval_strategy="temporal",
            ),
        ]
        service.vector_store.retrieve_by_ids = AsyncMock(return_value={})

        updated = await service._attach_vector_scores("test", results)

        assert updated[0].vector_score is None
        assert updated[0].retrieval_strategy == "temporal"  # preserved


# ═══════════════════════════════════════════════════════════════
#  3e/1s/1h per function: _direct_strategy_search
# ═══════════════════════════════════════════════════════════════


class TestDirectStrategySearch:
    """3e/1s/1h for _direct_strategy_search."""

    @pytest.mark.asyncio()
    async def test_happy_explicit_semantic(self, service: MemoryService) -> None:
        """Happy: explicit 'semantic' strategy dispatches correctly."""
        from claude_memory.schema import SearchResult

        mock_result = SearchResult(
            id="s1",
            name="Sem",
            node_type="Entity",
            project_id="p",
            score=0.9,
            distance=0.1,
        )
        service.router.route = AsyncMock(return_value=[mock_result])

        result = await service._direct_strategy_search("what is Python", "semantic", 10, None, 7)

        assert len(result) == 1
        assert result[0].retrieval_strategy == "semantic"

    @pytest.mark.asyncio()
    async def test_sad_temporal_with_dict_results(self, service) -> None:
        """Sad: temporal returns dicts → converted to SearchResult with score attachment."""
        service.router.route = AsyncMock(
            return_value=[
                {"id": "t1", "name": "Temp1", "node_type": "Entity", "project_id": "p"},
            ]
        )
        service.vector_store.retrieve_by_ids = AsyncMock(return_value={"t1": 0.7})

        result = await service._direct_strategy_search("recent timeline", "temporal", 10, None, 7)

        assert len(result) == 1
        assert result[0].retrieval_strategy == "temporal"
        assert result[0].score == pytest.approx(0.7)

    @pytest.mark.asyncio()
    async def test_evil_invalid_strategy_raises(self, service: MemoryService) -> None:
        """Evil: invalid strategy string raises ValueError."""
        with pytest.raises(ValueError):
            await service._direct_strategy_search("test", "nonexistent_strategy", 10, None, 7)

    @pytest.mark.asyncio()
    async def test_evil_graph_results_no_vector_match(self, service) -> None:
        """Evil: graph-only results with no vector match → score stays 0.0."""
        service.router.route = AsyncMock(
            return_value=[
                {"id": "g1", "name": "Graph1"},
            ]
        )
        service.vector_store.retrieve_by_ids = AsyncMock(return_value={})  # no match

        result = await service._direct_strategy_search(
            "relationship query", "relational", 10, None, 7
        )

        assert result[0].score == 0.0
        assert result[0].vector_score is None
        assert result[0].retrieval_strategy == "relational"

    @pytest.mark.asyncio()
    async def test_evil_empty_query_via_router(self, service: MemoryService) -> None:
        """Evil: router returns empty for a valid strategy → empty results."""
        service.router.route = AsyncMock(return_value=[])

        result = await service._direct_strategy_search("test", "associative", 10, None, 7)

        assert result == []


# ═══════════════════════════════════════════════════════════════
#  I3: include_meta=True MCP tool test
# ═══════════════════════════════════════════════════════════════


class TestIncludeMetaEnvelope:
    """Test the HybridSearchResponse envelope via search_memory MCP tool."""

    @pytest.mark.asyncio()
    async def test_happy_include_meta_returns_hybrid_response(self) -> None:
        """include_meta=True with temporal intent returns metadata envelope."""
        from claude_memory.schema import SearchResult

        mock_result = SearchResult(
            id="t1",
            name="Timeline",
            node_type="Entity",
            project_id="p",
            score=0.9,
            distance=0.1,
        )

        with (
            patch("claude_memory.server.service") as mock_svc,
        ):
            mock_svc.search = AsyncMock(
                return_value={
                    "results": [mock_result],
                    "metadata": {
                        "detected_intent": "temporal",
                        "temporal_exhausted": True,
                        "temporal_window_days": 7,
                        "temporal_result_count": 1,
                        "channels": {},
                    },
                }
            )

            # Import inside patch to avoid server module side effects
            from claude_memory.server import search_memory

            result = await search_memory(
                query="what happened yesterday",
                include_meta=True,
            )

        # Should return HybridSearchResponse.model_dump()
        assert isinstance(result, dict)
        assert "results" in result
        assert "meta" in result
        assert result["meta"]["temporal_exhausted"] is True
        assert result["meta"]["temporal_window_days"] == 7
        assert result["meta"]["temporal_result_count"] == 1

    @pytest.mark.asyncio()
    async def test_happy_include_meta_false_returns_plain_list(self) -> None:
        """include_meta=False (default) returns plain list of dicts."""
        from claude_memory.schema import SearchResult

        mock_result = SearchResult(
            id="s1",
            name="Semantic",
            node_type="Entity",
            project_id="p",
            score=0.8,
            distance=0.2,
        )

        with patch("claude_memory.server.service") as mock_svc:
            mock_svc.search = AsyncMock(return_value={"results": [mock_result], "metadata": {}})
            from claude_memory.server import search_memory

            result = await search_memory(query="test query")

        assert isinstance(result, list)
        assert result[0]["id"] == "s1"
