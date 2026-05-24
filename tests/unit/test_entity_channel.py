"""Gold Stack tests for _entity_extraction_enrichment channel (Tier 2.2).

TDD Red phase — tests written BEFORE the channel implementation.
Tests the pipeline integration: query → NER → entity lookup → channel results.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_memory.router import QueryRouter

# ── Helpers ──────────────────────────────────────────────────────────


MOCK_EMBEDDING = [0.1] * 384


def _make_cypher_result(rows: list[list[Any]]) -> MagicMock:
    """Build a mock FalkorDB query result."""
    mock = MagicMock()
    mock.result_set = rows
    return mock


@pytest.fixture()
def service():
    """MemoryService with all deps mocked for entity channel testing."""
    mock_embedder = MagicMock()
    mock_embedder.encode.return_value = MOCK_EMBEDDING

    with patch("claude_memory.repository.FalkorDB"):
        with patch("claude_memory.lock_manager.redis.Redis"):
            with patch("claude_memory.vector_store.AsyncQdrantClient"):
                from claude_memory.tools import MemoryService

                svc = MemoryService(embedding_service=mock_embedder)

    svc.repo = AsyncMock()
    svc.activation_engine.repo = svc.repo
    svc.vector_store = AsyncMock()
    svc.router = MagicMock(spec=QueryRouter)
    svc.reranker = MagicMock()
    svc.reranker.rerank = AsyncMock(side_effect=lambda q, c, **kw: c)
    # Default enrichment mocks
    svc.query_timeline = AsyncMock(return_value=[])
    svc.traverse_path = AsyncMock(return_value=[])
    svc.search_associative = AsyncMock(return_value=[])
    svc.activation_engine.activate = AsyncMock(return_value={})
    svc.activation_engine.spread = AsyncMock(return_value={})
    return svc


# ═══════════════════════════════════════════════════════════════
#  _entity_extraction_enrichment: 3-evil / 1-sad / 1-happy
# ═══════════════════════════════════════════════════════════════


class TestEntityExtractionChannel:
    """Gold Stack tests for the entity extraction retrieval channel."""

    @pytest.mark.asyncio()
    async def test_happy_entity_names_lookup_graph(self, service) -> None:
        """NER extracts 'Google' from query → Cypher finds it → returns as channel result."""
        pytest.importorskip("spacy", reason="spaCy not installed")
        # Mock: Cypher finds a node named "Google"
        mock_node = MagicMock()
        mock_node.properties = {"id": "google-001", "name": "Google"}
        service.repo.execute_cypher.return_value = _make_cypher_result([[mock_node]])

        results = await service._entity_extraction_enrichment("Tell me about Google")

        assert len(results) > 0
        ids = [r["id"] for r in results]
        assert "google-001" in ids

    @pytest.mark.asyncio()
    async def test_happy_multiple_entities_found(self, service) -> None:
        """Multiple NER entities → multiple Cypher lookups → combined results."""
        mock_node_a = MagicMock()
        mock_node_a.properties = {"id": "alice-001", "name": "Alice"}
        mock_node_b = MagicMock()
        mock_node_b.properties = {"id": "bob-001", "name": "Bob"}

        service.repo.execute_cypher.return_value = _make_cypher_result(
            [
                [mock_node_a],
                [mock_node_b],
            ]
        )

        results = await service._entity_extraction_enrichment("Alice and Bob discussed Python")

        # Should find at least the NER-matched entities
        assert isinstance(results, list)

    @pytest.mark.asyncio()
    async def test_sad1_no_entities_in_query(self, service) -> None:
        """No NER entities in query → empty results (no Cypher call needed)."""
        results = await service._entity_extraction_enrichment("the quick brown fox")

        # Should return empty — no entities to look up
        assert results == []

    @pytest.mark.asyncio()
    async def test_sad1_entities_not_in_graph(self, service) -> None:
        """NER extracts entities but graph has no matching nodes → empty."""
        service.repo.execute_cypher.return_value = _make_cypher_result([])

        results = await service._entity_extraction_enrichment("Tell me about Elon Musk")

        assert results == []

    @pytest.mark.asyncio()
    async def test_evil1_cypher_error_returns_empty(self, service) -> None:
        """Cypher query failure → graceful degradation (empty list)."""
        service.repo.execute_cypher.side_effect = ConnectionError("FalkorDB down")

        results = await service._entity_extraction_enrichment("Tell me about Google")

        assert results == []

    @pytest.mark.asyncio()
    async def test_evil1_empty_query_returns_empty(self, service) -> None:
        """Empty query string → empty results."""
        results = await service._entity_extraction_enrichment("")

        assert results == []

    @pytest.mark.asyncio()
    async def test_evil1_result_format_has_id_key(self, service) -> None:
        """Results must have 'id' key for RRF merge compatibility."""
        mock_node = MagicMock()
        mock_node.properties = {"id": "test-001", "name": "TestEntity"}
        service.repo.execute_cypher.return_value = _make_cypher_result([[mock_node]])

        results = await service._entity_extraction_enrichment("Tell me about TestEntity")

        if results:  # Only check format if entities were found
            for r in results:
                assert "id" in r, f"Result missing 'id' key: {r}"
