"""Tests that search/CRUD results strip embedding vectors from output.

Embedding arrays (1024+ floats) must never leak into API responses.
These tests verify the stripping logic at each boundary.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_memory.schema import (
    EntityCreateParams,
    GetHologramParams,
    GetNeighborsParams,
    SearchMemoryParams,
    SearchResult,
)
from claude_memory.tools import MemoryService


@pytest.fixture
def mock_service():
    """Build a MemoryService with all deps mocked."""
    mock_embedder = MagicMock()
    mock_vector = AsyncMock()

    with patch("claude_memory.tools.MemoryRepository"):
        service = MemoryService(embedding_service=mock_embedder, vector_store=mock_vector)
    service.repo = AsyncMock()
    service.fts_store = MagicMock()
    service.fts_store.search = MagicMock(return_value=[])
    service.reranker = MagicMock()
    service.reranker.rerank = AsyncMock(side_effect=lambda q, c, **kw: c)
    # Soft routing
    service.query_timeline = AsyncMock(return_value=[])
    service.traverse_path = AsyncMock(return_value=[])
    service.activation_engine = MagicMock()
    service.activation_engine.activate = AsyncMock(return_value={})
    service.activation_engine.spread = AsyncMock(return_value={})

    # Prevent _fire_salience_update from spawning orphan background tasks
    service._fire_salience_update = MagicMock()
    return service


# ─── create_entity: embedding must not leak in receipt ──────────────


@pytest.mark.asyncio
async def test_happy_create_entity_strips_embedding_from_receipt(mock_service):
    """create_entity receipt must not contain the embedding array."""
    mock_service.repo.create_node.return_value = {
        "id": "123",
        "name": "Test",
        "node_type": "Entity",
        "embedding": [0.1] * 1024,  # THE LEAK
    }
    mock_service.repo.get_total_node_count.return_value = 1
    mock_service.repo.get_most_recent_entity.return_value = None
    mock_service.embedder.encode.return_value = [0.1] * 1024

    # Mock vector upsert (async)
    mock_service.vector_store.upsert = AsyncMock()
    # Mock lock manager
    with patch.object(mock_service, "lock_manager", MagicMock()):
        mock_service.lock_manager.acquire_write = AsyncMock(return_value=True)
        mock_service.lock_manager.release_write = AsyncMock()
        result = await mock_service.create_entity(
            EntityCreateParams(name="Test", node_type="Entity", project_id="test")
        )

    # The result dict should not contain 'embedding'
    assert "embedding" not in result


@pytest.mark.asyncio
async def test_sad1_create_entity_receipt_missing_embedding_key_evil():
    """Evil: what if repo returns NO embedding key? Should still work."""
    mock_embedder = MagicMock()
    mock_vector = AsyncMock()
    from unittest.mock import patch

    with patch("claude_memory.tools.MemoryRepository"):
        service = MemoryService(embedding_service=mock_embedder, vector_store=mock_vector)
    service.repo = AsyncMock()
    service.repo.create_node.return_value = {
        "id": "456",
        "name": "Clean",
        "node_type": "Entity",
    }
    service.repo.get_total_node_count.return_value = 1
    service.repo.get_most_recent_entity.return_value = None
    service.embedder.encode.return_value = [0.1] * 1024
    service.vector_store.upsert = AsyncMock()

    with patch.object(service, "lock_manager", MagicMock()):
        service.lock_manager.acquire_write = AsyncMock(return_value=True)
        service.lock_manager.release_write = AsyncMock()
        result = await service.create_entity(
            EntityCreateParams(name="Clean", node_type="Entity", project_id="test")
        )

    assert "embedding" not in result


# ─── search: embedding must not appear in SearchResult ──────────────


@pytest.mark.asyncio
async def test_happy_search_results_have_no_embedding_field(mock_service):
    """search() returns SearchResult models which have no embedding field."""
    mock_service.embedder.encode.return_value = [0.1] * 1024
    mock_service.vector_store.search = AsyncMock(return_value=[{"_id": "123", "_score": 0.9}])
    mock_service.repo.get_subgraph.return_value = {
        "nodes": [
            {
                "id": "123",
                "name": "Test",
                "node_type": "Entity",
                "project_id": "test",
                "embedding": [0.1] * 1024,
            }
        ],
        "edges": [],
    }
    mock_service._fire_salience_update = MagicMock()

    _res = await mock_service.search(SearchMemoryParams(query="test query"))
    results = _res.get("results", []) if isinstance(_res, dict) else _res

    assert len(results) == 1
    assert isinstance(results[0], SearchResult)
    # SearchResult model doesn't have embedding field — Pydantic strips it
    assert not hasattr(results[0], "embedding")


# ─── get_hologram: embedding must be stripped from raw dict ─────────


@pytest.mark.asyncio
async def test_happy_get_hologram_strips_embedding(mock_service):
    """get_hologram returns raw dicts — embedding must be popped."""
    mock_service.embedder.encode.return_value = [0.1] * 1024

    anchor_mock = MagicMock()
    anchor_mock.id = "1"
    anchor_mock.model_dump.return_value = {"id": "1", "name": "Anchor"}

    mock_service.search = AsyncMock(return_value=[anchor_mock])
    mock_service.repo.get_subgraph.return_value = {
        "nodes": [{"id": "1", "name": "LeakyNode", "embedding": [0.001] * 1536}],
        "edges": [],
    }
    mock_service.context_manager = MagicMock()
    mock_service.context_manager.optimize.return_value = [{"id": "1", "name": "LeakyNode"}]

    result = await mock_service.get_hologram(GetHologramParams(query="query", depth=1))

    nodes = result["nodes"]
    assert len(nodes) > 0
    assert "embedding" not in nodes[0], "Embedding field was leaked in output!"


# ─── get_neighbors: embedding must be stripped ──────────────────────


@pytest.mark.asyncio
async def test_happy_get_neighbors_strips_embedding(mock_service):
    """get_neighbors pops embedding from node properties."""
    mock_node = MagicMock()
    mock_node.properties = {"id": "1", "name": "Neighbor", "embedding": [0.1] * 1024}

    mock_res = MagicMock()
    mock_res.result_set = [[mock_node]]
    mock_service.repo.execute_cypher.return_value = mock_res

    neighbors = await mock_service.get_neighbors(GetNeighborsParams(entity_id="root_id"))

    assert len(neighbors) == 1
    assert "embedding" not in neighbors[0], "get_neighbors leaked embedding!"
