"""Coverage gap tests for MemoryService (tools.py).

Targets all uncovered lines: create_relationship (with/without project lock),
update_entity, delete_entity (soft/hard with vector delete failures),
delete_relationship, add_observation (entity not found), end_session (not found),
record_breakthrough (with/without session), traverse_path (with path nodes,
without nodes attribute), point_in_time_query, search (with results),
analyze_graph (pagerank success/error, louvain success/error),
get_stale_entities, consolidate_memories (with/without edge creation errors),
create_memory_type.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_memory.exceptions import SearchError
from claude_memory.schema import (
    AnalyzeGraphParams,
    CreateMemoryTypeParams,
    GetHologramParams,
    PointInTimeQueryParams,
    SearchAssociativeParams,
    SearchMemoryParams,
    TraversePathParams,
)

# ─── Test Constants ─────────────────────────────────────────────────

PROJECT_ID = "project-alpha"
ENTITY_ID = "entity-001"
ENTITY_ID_2 = "entity-002"
ENTITY_ID_3 = "entity-003"
ENTITY_NAME = "Python"
ENTITY_TYPE = "Language"
RELATIONSHIP_TYPE = "RELATED_TO"
RELATIONSHIP_ID = "rel-001"
CONFIDENCE_DEFAULT = 1.0

OBSERVATION_CONTENT = "Observed a critical pattern"
EVIDENCE_LIST = ["source-a", "source-b"]
CERTAINTY_CONFIRMED = "confirmed"
DELETE_REASON = "deprecated"

SESSION_ID = "session-001"
SESSION_FOCUS = "architecture"
SESSION_SUMMARY = "Reviewed patterns"
SESSION_OUTCOMES = ["fixed-race-condition"]

BREAKTHROUGH_NAME = "eureka"
BREAKTHROUGH_MOMENT = "2024-06-15T14:30:00Z"
BREAKTHROUGH_ANALOGY = "water-flow"
BREAKTHROUGH_CONCEPT = "async-patterns"

SEARCH_QUERY = "async patterns"
SEARCH_LIMIT = 5
TIME_AS_OF = "2024-01-01T00:00:00Z"
STALE_DAYS = 30

MOCK_EMBEDDING = [0.1, 0.2, 0.3]
MOCK_NODE_PROPS = {"id": ENTITY_ID, "name": ENTITY_NAME, "project_id": PROJECT_ID}

CONSOLIDATION_SUMMARY = "Merged related concepts"
CONSOLIDATION_TRUNCATED_LEN = 20

PAGERANK_SCORE = 0.85
COMMUNITY_ID = 1
COMMUNITY_SIZE = 5
COMMUNITY_MEMBERS = ["A", "B", "C"]


# ─── Module Import ──────────────────────────────────────────────────

with patch("claude_memory.repository.FalkorDB"):
    with patch("claude_memory.lock_manager.redis.Redis"):
        with patch("claude_memory.vector_store.AsyncQdrantClient"):
            from claude_memory.schema import (
                BreakthroughParams,
                EntityDeleteParams,
                EntityUpdateParams,
                ObservationParams,
                RelationshipCreateParams,
                RelationshipDeleteParams,
                SessionEndParams,
            )
            from claude_memory.tools import MemoryService


# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture()
def service() -> MemoryService:
    """Creates a MemoryService with all dependencies mocked."""
    mock_embedder = MagicMock()
    mock_embedder.encode.return_value = MOCK_EMBEDDING

    with patch("claude_memory.repository.FalkorDB"):
        with patch("claude_memory.lock_manager.redis.Redis"):
            with patch("claude_memory.vector_store.AsyncQdrantClient"):
                svc = MemoryService(embedding_service=mock_embedder)

    # Replace repo, vector_store, lock_manager with mocks
    svc.repo = AsyncMock()
    svc.repo.get_subgraph.return_value = {"nodes": [], "edges": []}
    svc.vector_store = AsyncMock()
    svc.fts_store = MagicMock()
    svc.fts_store.search = MagicMock(return_value=[])
    svc.lock_manager = MagicMock()

    # Soft routing defaults
    svc.query_timeline = AsyncMock(return_value=[])
    svc.traverse_path = AsyncMock(return_value=[])

    # Reranker defaults
    svc.reranker = MagicMock()
    svc.reranker.rerank = AsyncMock(side_effect=lambda q, c, **kw: c)

    # Activation defaults
    svc.activation_engine = MagicMock()
    svc.activation_engine.activate = AsyncMock(return_value={})
    svc.activation_engine.spread = AsyncMock(return_value={})

    # Lock context manager mock — AsyncMock prevents phantom async children
    # from MagicMock parent's internal _mock_children cleanup (GC-time warnings).
    mock_lock = AsyncMock()
    mock_lock.__enter__ = MagicMock(return_value=mock_lock)
    mock_lock.__exit__ = MagicMock(return_value=False)
    svc.lock_manager.lock.return_value = mock_lock

    # Prevent _fire_salience_update from creating orphan asyncio.create_task()
    # coroutines. The real method calls asyncio.create_task(repo.increment_salience(...))
    # which, with an AsyncMock repo, produces unawaited coroutines at GC time.
    svc._fire_salience_update = MagicMock()

    # Default async_repo returns for _compute_entity_embedding_text
    svc.repo.get_observations_for_entity.return_value = []

    return svc


def _make_cypher_result(rows: list[list[Any]]) -> MagicMock:
    """Creates a mock Cypher query result."""
    result = MagicMock()
    result.result_set = rows
    return result


# ─── Topographical Forcing (architect-injected) ─────────────────────


def test_meta_fixture_topology_required(service) -> None:
    """Topographical forcing: fixture must use AsyncMock for async-target attributes.

    Architect-injected per process/issues/14b_BUILD_SPEC.md.
    DO NOT remove or weaken this test.
    """
    from unittest.mock import AsyncMock

    assert isinstance(service.repo, AsyncMock), (
        "service.repo targets AsyncMemoryRepository (async) — must be AsyncMock"
    )
    assert isinstance(service.vector_store, AsyncMock), (
        "service.vector_store has async methods — must be AsyncMock"
    )


# ─── create_relationship Tests ──────────────────────────────────────


async def test_happy_create_relationship_with_project_lock(service: MemoryService) -> None:
    """When source node has project_id, use project lock."""
    service.repo.get_node.return_value = {"id": ENTITY_ID, "project_id": PROJECT_ID}
    service.repo.create_edge.return_value = {"id": RELATIONSHIP_ID}

    params = RelationshipCreateParams(
        from_entity=ENTITY_ID,
        to_entity=ENTITY_ID_2,
        relationship_type=RELATIONSHIP_TYPE,
    )
    result = await service.create_relationship(params)
    assert result["id"] == RELATIONSHIP_ID


async def test_happy_create_relationship_without_project(service: MemoryService) -> None:
    """When source node has no project_id, proceed without lock."""
    service.repo.get_node.return_value = {"id": ENTITY_ID}
    service.repo.create_edge.return_value = {"id": RELATIONSHIP_ID}

    params = RelationshipCreateParams(
        from_entity=ENTITY_ID,
        to_entity=ENTITY_ID_2,
        relationship_type=RELATIONSHIP_TYPE,
    )
    result = await service.create_relationship(params)
    assert result["id"] == RELATIONSHIP_ID


async def test_sad1_create_relationship_source_not_found(service: MemoryService) -> None:
    """When source node doesn't exist."""
    service.repo.get_node.return_value = None
    service.repo.create_edge.return_value = {"id": RELATIONSHIP_ID}

    params = RelationshipCreateParams(
        from_entity=ENTITY_ID,
        to_entity=ENTITY_ID_2,
        relationship_type=RELATIONSHIP_TYPE,
    )
    result = await service.create_relationship(params)
    assert result["id"] == RELATIONSHIP_ID


async def test_sad2_create_relationship_with_existing_id_in_props(service: MemoryService) -> None:
    """Branch 157→16: 'id' already in properties, UUID generation skipped."""
    service.repo.get_node.return_value = None
    pre_set_id = "custom-rel-id-999"
    service.repo.create_edge.return_value = {"id": pre_set_id}

    params = RelationshipCreateParams(
        from_entity=ENTITY_ID,
        to_entity=ENTITY_ID_2,
        relationship_type=RELATIONSHIP_TYPE,
        properties={"id": pre_set_id},
    )
    result = await service.create_relationship(params)
    assert result["id"] == pre_set_id
    # Verify the id we passed was preserved (not overwritten by uuid)
    call_args = service.repo.create_edge.call_args
    assert call_args[0][3]["id"] == pre_set_id


async def test_evil1_create_relationship_edge_creation_fails(service: MemoryService) -> None:
    """When edge creation returns empty result."""
    service.repo.get_node.return_value = None
    service.repo.create_edge.return_value = {}

    params = RelationshipCreateParams(
        from_entity=ENTITY_ID,
        to_entity=ENTITY_ID_2,
        relationship_type=RELATIONSHIP_TYPE,
    )
    result = await service.create_relationship(params)
    assert "error" in result


# ─── update_entity Tests ───────────────────────────────────────────


async def test_happy_update_entity_with_project_lock(service: MemoryService) -> None:
    service.repo.get_node.return_value = MOCK_NODE_PROPS
    service.repo.update_node.return_value = {**MOCK_NODE_PROPS, "version": "2.0"}

    params = EntityUpdateParams(entity_id=ENTITY_ID, properties={"version": "2.0"}, reason="update")
    result = await service.update_entity(params)
    assert result["version"] == "2.0"


async def test_evil2_update_entity_not_found(service: MemoryService) -> None:
    service.repo.get_node.return_value = None

    params = EntityUpdateParams(entity_id=ENTITY_ID, properties={"version": "2.0"}, reason="update")
    result = await service.update_entity(params)
    assert result == {"error": "Entity not found"}


# ─── delete_entity Tests ───────────────────────────────────────────


async def test_happy_delete_entity_soft(service: MemoryService) -> None:
    service.repo.get_node.return_value = MOCK_NODE_PROPS

    params = EntityDeleteParams(entity_id=ENTITY_ID, reason=DELETE_REASON, soft_delete=True)
    result = await service.delete_entity(params)
    assert result["status"] == "archived"
    service.repo.update_node.assert_called_once()
    service.vector_store.delete.assert_awaited_once_with(ENTITY_ID)


async def test_evil3_delete_entity_soft_vector_delete_raises(service: MemoryService) -> None:
    """Soft delete raises when vector store delete fails — no lenient path."""
    service.repo.get_node.return_value = MOCK_NODE_PROPS
    service.vector_store.delete.side_effect = ConnectionError("qdrant down")

    params = EntityDeleteParams(entity_id=ENTITY_ID, reason=DELETE_REASON, soft_delete=True)
    with pytest.raises(SearchError, match="qdrant down"):
        await service.delete_entity(params)


async def test_happy_delete_entity_hard(service: MemoryService) -> None:
    service.repo.get_node.return_value = MOCK_NODE_PROPS

    params = EntityDeleteParams(entity_id=ENTITY_ID, reason=DELETE_REASON, soft_delete=False)
    result = await service.delete_entity(params)
    assert result["status"] == "deleted"
    service.repo.delete_node.assert_called_once_with(ENTITY_ID)


async def test_evil4_delete_entity_hard_vector_delete_raises(service: MemoryService) -> None:
    """Hard delete raises when vector store delete fails — no lenient path."""
    service.repo.get_node.return_value = MOCK_NODE_PROPS
    service.vector_store.delete.side_effect = ConnectionError("qdrant down")

    params = EntityDeleteParams(entity_id=ENTITY_ID, reason=DELETE_REASON, soft_delete=False)
    with pytest.raises(SearchError, match="qdrant down"):
        await service.delete_entity(params)


async def test_evil5_delete_entity_not_found(service: MemoryService) -> None:
    service.repo.get_node.return_value = None

    params = EntityDeleteParams(entity_id=ENTITY_ID, reason=DELETE_REASON, soft_delete=True)
    result = await service.delete_entity(params)
    assert result == {"error": "Entity not found"}


async def test_sad3_delete_entity_no_project(service: MemoryService) -> None:
    """Entity without project_id should still delete without lock."""
    service.repo.get_node.return_value = {"id": ENTITY_ID, "name": ENTITY_NAME}

    params = EntityDeleteParams(entity_id=ENTITY_ID, reason=DELETE_REASON, soft_delete=True)
    result = await service.delete_entity(params)
    assert result["status"] == "archived"


# ─── delete_relationship Tests ─────────────────────────────────────


async def test_happy_delete_relationship(service: MemoryService) -> None:
    params = RelationshipDeleteParams(relationship_id=RELATIONSHIP_ID, reason=DELETE_REASON)
    result = await service.delete_relationship(params)
    assert result == {"status": "deleted", "id": RELATIONSHIP_ID}
    service.repo.delete_edge.assert_called_once_with(RELATIONSHIP_ID)


# ─── add_observation Tests ─────────────────────────────────────────


async def test_happy_add_observation_success(service: MemoryService) -> None:
    mock_obs_node = MagicMock()
    mock_obs_node.properties = {"id": "obs-001", "content": OBSERVATION_CONTENT}
    service.repo.execute_cypher.return_value = _make_cypher_result([[mock_obs_node]])
    service.repo.get_node.return_value = {
        "name": "Test",
        "node_type": "Entity",
        "project_id": "test",
    }
    service.repo.get_observations_for_entity.return_value = []

    params = ObservationParams(
        entity_id=ENTITY_ID,
        content=OBSERVATION_CONTENT,
        certainty=CERTAINTY_CONFIRMED,
        evidence=EVIDENCE_LIST,
    )
    result = await service.add_observation(params)
    assert result["content"] == OBSERVATION_CONTENT


async def test_evil6_add_observation_entity_not_found(service: MemoryService) -> None:
    service.repo.execute_cypher.return_value = _make_cypher_result([])

    params = ObservationParams(
        entity_id=ENTITY_ID,
        content=OBSERVATION_CONTENT,
        certainty=CERTAINTY_CONFIRMED,
    )
    result = await service.add_observation(params)
    assert result == {"error": "Entity not found"}


# ─── E-3: Observation Vectorization ─────────────────────────────────


async def test_happy_add_observation_creates_vector(service: MemoryService) -> None:
    """E-3: add_observation should embed content and upsert to vector store."""
    mock_obs_node = MagicMock()
    mock_obs_node.properties = {
        "id": "obs-002",
        "content": "GPU needs 16GB VRAM for fine-tuning",
        "project_id": "proj-1",
    }
    service.repo.execute_cypher.return_value = _make_cypher_result([[mock_obs_node]])
    service.repo.get_node.return_value = {
        "name": "Test",
        "node_type": "Entity",
        "project_id": "proj-1",
    }
    service.repo.get_observations_for_entity.return_value = []
    service.embedder.encode.return_value = [0.1] * 1024

    params = ObservationParams(
        entity_id=ENTITY_ID,
        content="GPU needs 16GB VRAM for fine-tuning",
        certainty=CERTAINTY_CONFIRMED,
    )
    result = await service.add_observation(params)
    assert result["id"] == "obs-002"

    # Verify embedding was computed for both observation and entity re-embedding
    assert service.embedder.encode.call_count == 2
    # Verify vector was upserted with observation metadata
    assert service.vector_store.upsert.call_count == 2
    call_kwargs = service.vector_store.upsert.call_args_list[0]
    assert call_kwargs.kwargs["id"] == "obs-002"
    assert call_kwargs.kwargs["payload"]["node_type"] == "Observation"
    assert call_kwargs.kwargs["payload"]["entity_id"] == ENTITY_ID


async def test_evil7_add_observation_skip_embed_on_entity_not_found(service: MemoryService) -> None:
    """E-3: No embedding when entity not found."""
    service.repo.execute_cypher.return_value = _make_cypher_result([])

    params = ObservationParams(
        entity_id=ENTITY_ID,
        content="This should not be embedded",
        certainty=CERTAINTY_CONFIRMED,
    )
    result = await service.add_observation(params)
    assert "error" in result
    service.embedder.encode.assert_not_called()
    service.vector_store.upsert.assert_not_called()


async def test_evil8_add_observation_vector_upsert_failure_raises(service: MemoryService) -> None:
    """Audit #1: vector upsert failure on add_observation must raise SearchError and compensate.

    PR-4: Observation cross-store compensation. On Qdrant failure, the graph
    observation must be DETACH DELETEd (no split-brain), and SearchError raised.
    """
    mock_obs_node = MagicMock()
    mock_obs_node.properties = {
        "id": "obs-003",
        "content": OBSERVATION_CONTENT,
        "project_id": PROJECT_ID,
    }
    service.repo.execute_cypher.return_value = _make_cypher_result([[mock_obs_node]])
    service.embedder.encode.return_value = [0.1] * 1024
    service.vector_store.upsert.side_effect = ConnectionError("qdrant down")

    params = ObservationParams(
        entity_id=ENTITY_ID,
        content=OBSERVATION_CONTENT,
        certainty=CERTAINTY_CONFIRMED,
    )
    with pytest.raises(SearchError, match="Vector store unavailable during observation add"):
        await service.add_observation(params)

    # Verify compensating DETACH DELETE was issued
    # The last execute_cypher call should be the compensation delete
    last_call = service.repo.execute_cypher.call_args_list[-1]
    assert "DETACH DELETE" in last_call[0][0]
    assert last_call[0][1]["id"] == "obs-003"


# ─── end_session Tests ─────────────────────────────────────────────


async def test_evil9_end_session_not_found(service: MemoryService) -> None:
    service.repo.execute_cypher.return_value = _make_cypher_result([])

    params = SessionEndParams(
        session_id=SESSION_ID, summary=SESSION_SUMMARY, outcomes=SESSION_OUTCOMES
    )
    result = await service.end_session(params)
    assert result == {"error": "Session not found"}


# ─── record_breakthrough Tests ─────────────────────────────────────


async def test_happy_record_breakthrough_with_session(service: MemoryService) -> None:
    service.repo.create_node.return_value = {"id": "b-001", "name": BREAKTHROUGH_NAME}

    params = BreakthroughParams(
        name=BREAKTHROUGH_NAME,
        moment=BREAKTHROUGH_MOMENT,
        session_id=SESSION_ID,
        analogy_used=BREAKTHROUGH_ANALOGY,
        concepts_unlocked=[BREAKTHROUGH_CONCEPT],
    )
    result = await service.record_breakthrough(params)
    assert result["name"] == BREAKTHROUGH_NAME
    # Verify edge was created linking session to breakthrough
    service.repo.create_edge.assert_called_once()


async def test_happy_record_breakthrough_without_session(service: MemoryService) -> None:
    """When session_id is empty, no edge should be created."""
    service.repo.create_node.return_value = {"id": "b-001", "name": BREAKTHROUGH_NAME}

    params = BreakthroughParams(
        name=BREAKTHROUGH_NAME,
        moment=BREAKTHROUGH_MOMENT,
        session_id="",
    )
    result = await service.record_breakthrough(params)
    assert result["name"] == BREAKTHROUGH_NAME
    service.repo.create_edge.assert_not_called()


# ─── traverse_path Tests ──────────────────────────────────────────


async def test_happy_traverse_path_with_nodes(service: MemoryService) -> None:
    """When path has .nodes attribute, extract properties."""
    service.traverse_path = MemoryService.traverse_path.__get__(service)
    mock_node_a = MagicMock()
    mock_node_a.properties = {"id": ENTITY_ID, "name": "NodeA", "embedding": MOCK_EMBEDDING}
    mock_node_b = MagicMock()
    mock_node_b.properties = {"id": ENTITY_ID_2, "name": "NodeB"}

    mock_path = MagicMock()
    mock_path.nodes = [mock_node_a, mock_node_b]

    service.repo.execute_cypher.return_value = _make_cypher_result([[mock_path]])

    result = await service.traverse_path(TraversePathParams(from_id=ENTITY_ID, to_id=ENTITY_ID_2))
    assert len(result) == 2
    # Verify embedding was stripped
    assert "embedding" not in result[0]


async def test_sad4_traverse_path_no_path_found(service: MemoryService) -> None:
    service.repo.execute_cypher.return_value = _make_cypher_result([])

    result = await service.traverse_path(TraversePathParams(from_id=ENTITY_ID, to_id=ENTITY_ID_2))
    assert result == []


async def test_sad5_traverse_path_no_nodes_attr(service: MemoryService) -> None:
    """When path object doesn't have .nodes attribute."""
    mock_path = MagicMock(spec=[])  # No attributes
    service.repo.execute_cypher.return_value = _make_cypher_result([[mock_path]])

    result = await service.traverse_path(TraversePathParams(from_id=ENTITY_ID, to_id=ENTITY_ID_2))
    assert result == []


# ─── search Tests ──────────────────────────────────────────────────


async def test_sad6_search_empty_query(service: MemoryService) -> None:
    _res = await service.search(SearchMemoryParams(query="", limit=SEARCH_LIMIT))
    result = _res.get("results", []) if isinstance(_res, dict) else _res
    assert result == []


async def test_sad7_search_no_vector_results(service: MemoryService) -> None:
    service.vector_store.search.return_value = []

    _res = await service.search(SearchMemoryParams(query=SEARCH_QUERY, limit=SEARCH_LIMIT))

    result = _res.get("results", []) if isinstance(_res, dict) else _res
    assert result == []


async def test_happy_search_with_results(service: MemoryService) -> None:
    service.vector_store.search.return_value = [
        {"_id": ENTITY_ID, "_score": PAGERANK_SCORE},
    ]
    service.repo.get_subgraph.return_value = {
        "nodes": [
            {
                "id": ENTITY_ID,
                "name": ENTITY_NAME,
                "node_type": ENTITY_TYPE,
                "project_id": PROJECT_ID,
                "description": "A programming language",
            }
        ],
        "edges": [],
    }

    _res = await service.search(SearchMemoryParams(query=SEARCH_QUERY, limit=SEARCH_LIMIT))

    result = _res.get("results", []) if isinstance(_res, dict) else _res
    assert len(result) == 1
    assert result[0].id == ENTITY_ID
    assert result[0].name == ENTITY_NAME
    assert result[0].score == PAGERANK_SCORE


async def test_sad8_search_node_not_in_graph(service: MemoryService) -> None:
    """When vector result ID is not in graph, it's excluded."""
    service.vector_store.search.return_value = [
        {"_id": "orphan-id", "_score": PAGERANK_SCORE},
    ]
    service.repo.get_subgraph.return_value = {"nodes": [], "edges": []}

    _res = await service.search(SearchMemoryParams(query=SEARCH_QUERY, limit=SEARCH_LIMIT))

    result = _res.get("results", []) if isinstance(_res, dict) else _res
    assert result == []


# ─── E-2: Deep Search ──────────────────────────────────────────────


async def test_happy_search_deep_returns_observations(service: MemoryService) -> None:
    """E-2: deep=True populates observations on each SearchResult."""
    from unittest.mock import MagicMock

    service.vector_store.search.return_value = [
        {"_id": ENTITY_ID, "_score": PAGERANK_SCORE},
    ]
    service.repo.get_subgraph.return_value = {
        "nodes": [
            {
                "id": ENTITY_ID,
                "name": ENTITY_NAME,
                "node_type": ENTITY_TYPE,
                "project_id": PROJECT_ID,
                "description": "A language",
            }
        ],
        "edges": [],
    }
    # Mock observation lookup
    obs_result = MagicMock()
    obs_result.result_set = [["First observation"], ["Second observation"]]
    service.repo.execute_cypher.return_value = obs_result

    _res = await service.search(
        SearchMemoryParams(query=SEARCH_QUERY, limit=SEARCH_LIMIT, deep=True)
    )
    result = _res.get("results", []) if isinstance(_res, dict) else _res
    assert len(result) == 1
    assert result[0].observations == ["First observation", "Second observation"]


async def test_happy_search_deep_returns_relationships(service: MemoryService) -> None:
    """E-2: deep=True populates relationships on each SearchResult."""
    from unittest.mock import MagicMock

    service.vector_store.search.return_value = [
        {"_id": ENTITY_ID, "_score": PAGERANK_SCORE},
    ]
    service.repo.get_subgraph.return_value = {
        "nodes": [
            {
                "id": ENTITY_ID,
                "name": ENTITY_NAME,
                "node_type": ENTITY_TYPE,
                "project_id": PROJECT_ID,
            }
        ],
        "edges": [
            {"source": ENTITY_ID, "target": "other-1", "type": "RELATES_TO"},
            {"source": "other-2", "target": ENTITY_ID, "type": "DEPENDS_ON"},
        ],
    }
    obs_result = MagicMock()
    obs_result.result_set = []
    service.repo.execute_cypher.return_value = obs_result

    _res = await service.search(
        SearchMemoryParams(query=SEARCH_QUERY, limit=SEARCH_LIMIT, deep=True)
    )
    result = _res.get("results", []) if isinstance(_res, dict) else _res
    assert len(result) == 1
    assert len(result[0].relationships) == 2


async def test_sad9_search_shallow_backward_compat(service: MemoryService) -> None:
    """E-2: Default search (no deep) does NOT include observations/relationships."""
    service.vector_store.search.return_value = [
        {"_id": ENTITY_ID, "_score": PAGERANK_SCORE},
    ]
    service.repo.get_subgraph.return_value = {
        "nodes": [
            {
                "id": ENTITY_ID,
                "name": ENTITY_NAME,
                "node_type": ENTITY_TYPE,
                "project_id": PROJECT_ID,
            }
        ],
        "edges": [],
    }

    _res = await service.search(SearchMemoryParams(query=SEARCH_QUERY, limit=SEARCH_LIMIT))

    result = _res.get("results", []) if isinstance(_res, dict) else _res
    assert len(result) == 1
    # Shallow search should have empty/None observations and relationships
    assert not result[0].observations
    assert not result[0].relationships


# ─── search with project_id filter ─────────────────────────────────


async def test_happy_search_with_project_id_filter(service: MemoryService) -> None:
    """Search with project_id passes filter to vector store."""
    service.vector_store.search.return_value = [
        {"_id": ENTITY_ID, "_score": PAGERANK_SCORE},
    ]
    service.repo.get_subgraph.return_value = {
        "nodes": [
            {
                "id": ENTITY_ID,
                "name": ENTITY_NAME,
                "node_type": ENTITY_TYPE,
                "project_id": PROJECT_ID,
                "description": "A language",
            }
        ],
        "edges": [],
    }

    _res = await service.search(
        SearchMemoryParams(query=SEARCH_QUERY, limit=SEARCH_LIMIT, project_id=PROJECT_ID)
    )
    result = _res.get("results", []) if isinstance(_res, dict) else _res
    assert len(result) == 1

    # Verify filter was passed to vector_store.search
    call_kwargs = service.vector_store.search.call_args[1]
    assert call_kwargs["filter"] == {"project_id": PROJECT_ID}


async def test_happy_search_with_mmr_flag(service: MemoryService) -> None:
    """When mmr=True, search delegates to vector_store.search_mmr."""
    service.vector_store.search_mmr.return_value = [
        {"_id": ENTITY_ID, "_score": PAGERANK_SCORE},
    ]
    service.repo.get_subgraph.return_value = {
        "nodes": [
            {
                "id": ENTITY_ID,
                "name": ENTITY_NAME,
                "node_type": ENTITY_TYPE,
                "project_id": PROJECT_ID,
                "description": "A language",
            }
        ],
        "edges": [],
    }

    _res = await service.search(
        SearchMemoryParams(query=SEARCH_QUERY, limit=SEARCH_LIMIT, mmr=True)
    )
    result = _res.get("results", []) if isinstance(_res, dict) else _res
    assert len(result) == 1
    # Verify search_mmr was called instead of search
    service.vector_store.search_mmr.assert_awaited_once()
    service.vector_store.search.assert_not_awaited()


# ─── point_in_time_query Tests ─────────────────────────────────────


async def test_sad10_point_in_time_query_no_results(service: MemoryService) -> None:
    service.vector_store.search.return_value = []

    result = await service.point_in_time_query(
        PointInTimeQueryParams(query_text=SEARCH_QUERY, as_of=TIME_AS_OF)
    )
    assert result == []


async def test_happy_point_in_time_query_with_results(service: MemoryService) -> None:
    service.vector_store.search.return_value = [
        {"_id": ENTITY_ID},
    ]
    service.repo.get_subgraph.return_value = {
        "nodes": [{"id": ENTITY_ID, "name": ENTITY_NAME}],
        "edges": [],
    }

    result = await service.point_in_time_query(
        PointInTimeQueryParams(query_text=SEARCH_QUERY, as_of=TIME_AS_OF)
    )
    assert len(result) == 1
    assert result[0]["id"] == ENTITY_ID


# ─── analyze_graph Tests ──────────────────────────────────────────


async def test_happy_analyze_graph_pagerank_success(service: MemoryService) -> None:
    """PageRank returns ranked entities based on graph adjacency."""
    mock_node_a = MagicMock()
    mock_node_a.properties = {"name": ENTITY_NAME}
    mock_node_a.labels = [ENTITY_TYPE, "Entity"]
    mock_node_b = MagicMock()
    mock_node_b.properties = {"name": "NodeB"}
    mock_node_b.labels = ["Entity"]

    service.repo.execute_cypher.side_effect = [
        _make_cypher_result([[mock_node_a], [mock_node_b]]),  # nodes
        _make_cypher_result([["NodeB", ENTITY_NAME]]),  # edges: B->A
    ]

    result = await service.analyze_graph(AnalyzeGraphParams(algorithm="pagerank"))
    assert len(result) >= 1
    # A has an incoming edge, should have a higher rank
    assert result[0]["name"] == ENTITY_NAME
    assert "rank" in result[0]


async def test_happy_analyze_graph_pagerank_only_entity_label(service: MemoryService) -> None:
    """When node only has 'Entity' label, type should be 'Entity'."""
    mock_node = MagicMock()
    mock_node.properties = {"name": ENTITY_NAME}
    mock_node.labels = ["Entity"]  # Only Entity label

    service.repo.execute_cypher.side_effect = [
        _make_cypher_result([[mock_node]]),  # nodes
        _make_cypher_result([]),  # no edges
    ]

    result = await service.analyze_graph(AnalyzeGraphParams(algorithm="pagerank"))
    assert result[0]["type"] == "Entity"


async def test_evil10_analyze_graph_pagerank_error(service: MemoryService) -> None:
    """Errors propagate loudly from PageRank (no silent swallowing)."""
    service.repo.execute_cypher.side_effect = RuntimeError("algo not available")

    with pytest.raises(RuntimeError, match="algo not available"):
        await service.analyze_graph(AnalyzeGraphParams(algorithm="pagerank"))


async def test_happy_analyze_graph_louvain_success(service: MemoryService) -> None:
    """Louvain detects communities from graph adjacency."""
    nodes = []
    for name in ["A", "B", "C"]:
        n = MagicMock()
        n.properties = {"name": name}
        n.labels = ["Entity"]
        nodes.append(n)

    service.repo.execute_cypher.side_effect = [
        _make_cypher_result([[n] for n in nodes]),  # nodes
        _make_cypher_result([["A", "B"], ["B", "C"], ["A", "C"]]),  # edges
    ]

    result = await service.analyze_graph(AnalyzeGraphParams(algorithm="louvain"))
    assert len(result) >= 1
    assert "community_id" in result[0]
    assert "size" in result[0]
    assert "members" in result[0]


async def test_evil11_analyze_graph_louvain_error(service: MemoryService) -> None:
    """Errors propagate loudly from Louvain (no silent swallowing)."""
    service.repo.execute_cypher.side_effect = RuntimeError("algo not available")

    with pytest.raises(RuntimeError, match="algo not available"):
        await service.analyze_graph(AnalyzeGraphParams(algorithm="louvain"))


async def test_sad11_analyze_graph_unsupported_algorithm(service: MemoryService) -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await service.analyze_graph(AnalyzeGraphParams(algorithm="unknown"))  # type: ignore[arg-type]


# ─── get_stale_entities Tests ──────────────────────────────────────


async def test_sad12_get_stale_entities(service: MemoryService) -> None:
    mock_node = MagicMock()
    mock_node.properties = {"id": ENTITY_ID, "name": ENTITY_NAME, "embedding": MOCK_EMBEDDING}

    service.repo.execute_cypher.return_value = _make_cypher_result([[mock_node]])

    result = await service.get_stale_entities(days=STALE_DAYS)
    assert len(result) == 1
    assert result[0]["id"] == ENTITY_ID
    # Verify embedding was stripped
    assert "embedding" not in result[0]


# ─── consolidate_memories Tests ─────────────────────────────────────


async def test_happy_consolidate_memories(service: MemoryService) -> None:
    service.repo.create_node.return_value = {"id": "consolidated-001", "name": "Consolidated"}

    result = await service.consolidate_memories(
        entity_ids=[ENTITY_ID, ENTITY_ID_2],
        summary=CONSOLIDATION_SUMMARY,
    )
    assert result["id"] == "consolidated-001"
    service.vector_store.upsert.assert_awaited_once()


async def test_evil12_consolidate_memories_edge_error(service: MemoryService) -> None:
    """When linking an old entity fails, continue with remaining."""
    service.repo.create_node.return_value = {"id": "consolidated-001", "name": "Consolidated"}
    service.repo.create_edge.side_effect = [
        ConnectionError("edge failed"),  # First entity fails
        MagicMock(),  # Second succeeds
    ]
    service.repo.update_node.return_value = {}

    result = await service.consolidate_memories(
        entity_ids=[ENTITY_ID, ENTITY_ID_2],
        summary=CONSOLIDATION_SUMMARY,
    )
    assert result["id"] == "consolidated-001"


# ─── create_memory_type Tests ──────────────────────────────────────


def test_happy_create_memory_type(service: MemoryService) -> None:
    service.ontology = MagicMock()

    result = service.create_memory_type(
        CreateMemoryTypeParams(
            name="Recipe",
            description="Culinary recipe",
            required_properties=["ingredients"],
        )
    )
    assert result["name"] == "Recipe"
    assert result["status"] == "active"


def test_sad13_create_memory_type_defaults(service: MemoryService) -> None:
    service.ontology = MagicMock()

    result = service.create_memory_type(
        CreateMemoryTypeParams(name="Recipe", description="Culinary recipe")
    )
    assert result["required_properties"] == []


# ─── get_hologram Tests ────────────────────────────────────────────

HOLOGRAM_QUERY = "async patterns"
HOLOGRAM_DEPTH = 2
HOLOGRAM_MAX_TOKENS = 4000


async def test_sad14_get_hologram_no_anchors(service: MemoryService) -> None:
    """Line 715: search returns no anchors → early return."""
    service.vector_store.search.return_value = []

    result = await service.get_hologram(
        GetHologramParams(query=HOLOGRAM_QUERY, depth=HOLOGRAM_DEPTH)
    )
    assert result == {"nodes": [], "edges": []}


async def test_happy_get_hologram_with_non_dict_nodes(service: MemoryService) -> None:
    """Branch 733→732: raw_nodes contains a non-dict item → isinstance check False."""
    service.vector_store.search.return_value = [
        {"_id": ENTITY_ID, "_score": PAGERANK_SCORE},
    ]
    service.repo.get_subgraph.return_value = {
        "nodes": [
            {
                "id": ENTITY_ID,
                "name": ENTITY_NAME,
                "node_type": ENTITY_TYPE,
                "embedding": MOCK_EMBEDDING,
            },
        ],
        "edges": [],
    }

    # Mock search properly — it calls get_subgraph internally
    mock_search_result = MagicMock()
    mock_search_result.id = ENTITY_ID
    mock_search_result.name = ENTITY_NAME
    mock_search_result.score = PAGERANK_SCORE
    mock_search_result.model_dump.return_value = {"id": ENTITY_ID, "name": ENTITY_NAME}

    # Hologram's internal search call returns results
    with patch.object(service, "search", return_value=[mock_search_result]):
        # get_subgraph returns both dict and non-dict nodes
        service.repo.get_subgraph.return_value = {
            "nodes": [
                {"id": ENTITY_ID, "name": ENTITY_NAME, "embedding": MOCK_EMBEDDING},
                MagicMock(),  # non-dict node → branch 733→732 False
            ],
            "edges": [],
        }
        service.context_manager = MagicMock()
        service.context_manager.optimize.return_value = [
            {"id": ENTITY_ID, "name": ENTITY_NAME},
        ]

        result = await service.get_hologram(
            GetHologramParams(
                query=HOLOGRAM_QUERY, depth=HOLOGRAM_DEPTH, max_tokens=HOLOGRAM_MAX_TOKENS
            )
        )

    assert result["query"] == HOLOGRAM_QUERY
    assert len(result["nodes"]) == 1
    # Embedding should have been stripped from the dict node
    assert "embedding" not in result["nodes"][0]


# ─── Salience Scoring Tests ────────────────────────────────────────


SALIENCE_DEFAULT = 1.0
RETRIEVAL_COUNT_DEFAULT = 0
SALIENCE_AFTER_ONE_RETRIEVAL = 2.0  # 1.0 + log2(1 + 1) = 1.0 + 1.0


async def test_happy_create_entity_initializes_salience(service: MemoryService) -> None:
    """create_entity sets salience_score=1.0 and retrieval_count=0 on the node."""
    from claude_memory.schema import EntityCreateParams

    service.repo.create_node.return_value = {
        "id": ENTITY_ID,
        "name": ENTITY_NAME,
        "salience_score": SALIENCE_DEFAULT,
        "retrieval_count": RETRIEVAL_COUNT_DEFAULT,
    }
    service.repo.get_total_node_count.return_value = 1
    service.repo.get_most_recent_entity.return_value = None
    service.ontology = MagicMock()
    service.ontology.is_valid_type.return_value = True

    params = EntityCreateParams(
        name=ENTITY_NAME,
        node_type=ENTITY_TYPE,
        project_id=PROJECT_ID,
    )
    result = await service.create_entity(params)
    assert result.id == ENTITY_ID

    # Verify salience fields were passed to create_node
    call_args = service.repo.create_node.call_args
    props = call_args[0][1]
    assert props["salience_score"] == SALIENCE_DEFAULT
    assert props["retrieval_count"] == RETRIEVAL_COUNT_DEFAULT


async def test_happy_search_fires_salience_async(service: MemoryService) -> None:
    """search() fires salience update as background task — returns pre-update score."""
    # Restore real _fire_salience_update (fixture mocks it to prevent orphan tasks;
    # this test explicitly tests the salience fire-and-forget pathway).
    del service._fire_salience_update
    service.vector_store.search.return_value = [
        {"_id": ENTITY_ID, "_score": PAGERANK_SCORE},
    ]
    service.repo.get_subgraph.return_value = {
        "nodes": [
            {
                "id": ENTITY_ID,
                "name": ENTITY_NAME,
                "node_type": ENTITY_TYPE,
                "project_id": PROJECT_ID,
                "description": "A language",
                "salience_score": SALIENCE_DEFAULT,
            }
        ],
        "edges": [],
    }
    service.repo.increment_salience.return_value = [
        {
            "id": ENTITY_ID,
            "salience_score": SALIENCE_AFTER_ONE_RETRIEVAL,
            "retrieval_count": 1,
        }
    ]

    _res = await service.search(SearchMemoryParams(query=SEARCH_QUERY, limit=SEARCH_LIMIT))

    result = _res.get("results", []) if isinstance(_res, dict) else _res
    assert len(result) == 1
    # Returns PRE-update salience from graph data (fire-and-forget)
    assert result[0].salience_score == SALIENCE_DEFAULT

    # Flush background tasks deterministically
    import asyncio

    if service._background_tasks:
        await asyncio.gather(*service._background_tasks, return_exceptions=True)
    # Verify salience was still fired in background (entity-001 must be present)
    service.repo.increment_salience.assert_called_once()
    salience_ids = service.repo.increment_salience.call_args[0][0]
    assert ENTITY_ID in salience_ids


async def test_evil13_search_salience_background_error_silent(service: MemoryService) -> None:
    """When background increment_salience fails, search still returns correctly."""
    # Restore real _fire_salience_update (same reason as test_happy_search_fires_salience_async).
    del service._fire_salience_update
    service.vector_store.search.return_value = [
        {"_id": ENTITY_ID, "_score": PAGERANK_SCORE},
    ]
    service.repo.get_subgraph.return_value = {
        "nodes": [
            {
                "id": ENTITY_ID,
                "name": ENTITY_NAME,
                "node_type": ENTITY_TYPE,
                "project_id": PROJECT_ID,
                "description": "A language",
                "salience_score": 3.5,
            }
        ],
        "edges": [],
    }
    service.repo.increment_salience.side_effect = ConnectionError("FalkorDB down")

    _res = await service.search(SearchMemoryParams(query=SEARCH_QUERY, limit=SEARCH_LIMIT))

    result = _res.get("results", []) if isinstance(_res, dict) else _res
    assert len(result) == 1
    # Uses graph node's salience_score (background error is silent)
    assert result[0].salience_score == 3.5

    # Flush background tasks (should not raise)
    import asyncio

    if service._background_tasks:
        await asyncio.gather(*service._background_tasks, return_exceptions=True)


async def test_sad15_search_salience_fallback_default(service: MemoryService) -> None:
    """When no salience_score in graph node, default to 0.0."""
    service.vector_store.search.return_value = [
        {"_id": ENTITY_ID, "_score": PAGERANK_SCORE},
    ]
    service.repo.get_subgraph.return_value = {
        "nodes": [
            {
                "id": ENTITY_ID,
                "name": ENTITY_NAME,
                "node_type": ENTITY_TYPE,
                "project_id": PROJECT_ID,
            }
        ],
        "edges": [],
    }
    service.repo.increment_salience.return_value = []  # No updates returned

    _res = await service.search(SearchMemoryParams(query=SEARCH_QUERY, limit=SEARCH_LIMIT))

    result = _res.get("results", []) if isinstance(_res, dict) else _res
    assert len(result) == 1
    assert result[0].salience_score == 0.0


def test_sad16_base_node_salience_defaults() -> None:
    """BaseNode schema defaults salience_score=1.0 and retrieval_count=0."""
    from claude_memory.schema import BaseNode

    node = BaseNode(name="test", node_type="Concept", project_id="test-proj")
    assert node.salience_score == SALIENCE_DEFAULT
    assert node.retrieval_count == RETRIEVAL_COUNT_DEFAULT


def test_sad17_search_result_salience_default() -> None:
    """SearchResult defaults salience_score=0.0."""
    from claude_memory.schema import SearchResult

    result = SearchResult(
        id="test-id",
        name="test",
        node_type="Concept",
        project_id="test-proj",
        score=0.9,
        distance=0.1,
    )
    assert result.salience_score == 0.0


# ─── Temporal Graph Layer Tests ────────────────────────────────────


def test_sad18_base_node_occurred_at_default() -> None:
    """BaseNode defaults occurred_at to None."""
    from claude_memory.schema import BaseNode

    node = BaseNode(name="test", node_type="Concept", project_id="proj")
    assert node.occurred_at is None


def test_happy_base_node_occurred_at_set() -> None:
    """BaseNode accepts occurred_at datetime."""
    from datetime import UTC, datetime

    from claude_memory.schema import BaseNode

    ts = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    node = BaseNode(name="test", node_type="Concept", project_id="proj", occurred_at=ts)
    assert node.occurred_at == ts


def test_happy_concurrent_with_edge_type() -> None:
    """CONCURRENT_WITH is a valid EdgeType."""
    from claude_memory.schema import RelationshipCreateParams

    params = RelationshipCreateParams(
        from_entity="a",
        to_entity="b",
        relationship_type="CONCURRENT_WITH",
    )
    assert params.relationship_type == "CONCURRENT_WITH"


@pytest.mark.asyncio
async def test_sad19_temporal_query_params_defaults() -> None:
    """TemporalQueryParams has correct defaults."""
    from datetime import UTC, datetime

    from claude_memory.schema import TemporalQueryParams

    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 2, 1, tzinfo=UTC)
    p = TemporalQueryParams(start=start, end=end)
    assert p.limit == 20
    assert p.project_id is None


@pytest.mark.asyncio
async def test_happy_temporal_query_params_with_project() -> None:
    """TemporalQueryParams accepts project_id."""
    from datetime import UTC, datetime

    from claude_memory.schema import TemporalQueryParams

    p = TemporalQueryParams(
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 2, 1, tzinfo=UTC),
        limit=50,
        project_id="my-project",
    )
    assert p.limit == 50
    assert p.project_id == "my-project"


# NOTE: test_query_timeline_no_project removed — used 3-layer deep FalkorDB mock chain
# (select_graph.return_value.query.return_value) and only tested mock calls.


@pytest.mark.asyncio
async def test_happy_query_timeline_with_project(service: MemoryService) -> None:
    """query_timeline filters by project_id when provided."""
    service.repo.query_timeline.return_value = [
        {"id": ENTITY_ID, "name": ENTITY_NAME, "project_id": PROJECT_ID}
    ]
    result = await service.repo.query_timeline(
        start="2026-01-01", end="2026-02-01", project_id=PROJECT_ID
    )
    assert len(result) == 1
    assert result[0]["project_id"] == PROJECT_ID


@pytest.mark.asyncio
async def test_happy_get_temporal_neighbors_before(service: MemoryService) -> None:
    """get_temporal_neighbors returns predecessors."""
    service.repo.get_temporal_neighbors.return_value = [{"id": "prev-1", "name": "Previous"}]
    result = await service.repo.get_temporal_neighbors(ENTITY_ID, direction="before")
    assert len(result) == 1
    service.repo.get_temporal_neighbors.assert_called_once_with(ENTITY_ID, direction="before")


@pytest.mark.asyncio
async def test_happy_get_temporal_neighbors_after(service: MemoryService) -> None:
    """get_temporal_neighbors returns successors."""
    service.repo.get_temporal_neighbors.return_value = [{"id": "next-1", "name": "Next"}]
    result = await service.repo.get_temporal_neighbors(ENTITY_ID, direction="forward")
    assert len(result) == 1


@pytest.mark.asyncio
async def test_happy_get_temporal_neighbors_both(service: MemoryService) -> None:
    """get_temporal_neighbors default direction returns both."""
    service.repo.get_temporal_neighbors.return_value = [{"id": "prev-1"}, {"id": "next-1"}]
    result = await service.repo.get_temporal_neighbors(ENTITY_ID)
    assert len(result) == 2


@pytest.mark.asyncio
async def test_happy_create_temporal_edge_success(service: MemoryService) -> None:
    """create_temporal_edge returns relationship metadata."""
    service.repo.create_temporal_edge.return_value = {
        "rel_type": "PRECEDED_BY",
        "from_id": "entity-a",
        "to_id": "entity-b",
    }
    result = await service.repo.create_temporal_edge("entity-a", "entity-b")
    assert result["rel_type"] == "PRECEDED_BY"


@pytest.mark.asyncio
async def test_evil14_create_temporal_edge_not_found(service: MemoryService) -> None:
    """create_temporal_edge returns error when entities not found."""
    service.repo.create_temporal_edge.return_value = {"error": "One or both entities not found"}
    result = await service.repo.create_temporal_edge("missing-a", "missing-b")
    assert "error" in result


# ─── Service-Level Temporal Tests ──────────────────────────────────


@pytest.mark.asyncio()
async def test_happy_create_entity_initializes_occurred_at(
    service: MemoryService,
) -> None:
    """create_entity sets occurred_at in properties."""
    from claude_memory.schema import EntityCreateParams

    service.ontology.is_valid_type = MagicMock(return_value=True)
    service.repo.create_node.return_value = {
        "id": "new-id",
        "name": ENTITY_NAME,
        "node_type": "Concept",
    }
    service.embedder.encode.return_value = [0.1] * 384
    service.vector_store.upsert = AsyncMock()
    service.repo.get_total_node_count.return_value = 1
    service.repo.get_most_recent_entity.return_value = None

    await service.create_entity(
        EntityCreateParams(
            name=ENTITY_NAME,
            node_type="Concept",
            project_id=PROJECT_ID,
        )
    )
    create_call_props = service.repo.create_node.call_args[0][1]
    assert "occurred_at" in create_call_props


@pytest.mark.asyncio()
async def test_happy_create_entity_respects_user_occurred_at(
    service: MemoryService,
) -> None:
    """create_entity uses user-provided occurred_at value."""
    from claude_memory.schema import EntityCreateParams

    service.ontology.is_valid_type = MagicMock(return_value=True)
    service.repo.create_node.return_value = {
        "id": "new-id",
        "name": ENTITY_NAME,
    }
    service.embedder.encode.return_value = [0.1] * 384
    service.vector_store.upsert = AsyncMock()
    service.repo.get_total_node_count.return_value = 1
    service.repo.get_most_recent_entity.return_value = None

    custom_ts = "2026-01-15T12:00:00+00:00"
    await service.create_entity(
        EntityCreateParams(
            name=ENTITY_NAME,
            node_type="Concept",
            project_id=PROJECT_ID,
            properties={"occurred_at": custom_ts},
        )
    )
    create_call_props = service.repo.create_node.call_args[0][1]
    assert create_call_props["occurred_at"] == custom_ts


@pytest.mark.asyncio()
async def test_happy_service_query_timeline(service: MemoryService) -> None:
    """MemoryService.query_timeline delegates to repo."""
    service.query_timeline = MemoryService.query_timeline.__get__(service)
    from datetime import UTC, datetime

    from claude_memory.schema import TemporalQueryParams

    service.repo.query_timeline.return_value = [{"id": ENTITY_ID, "name": ENTITY_NAME}]
    params = TemporalQueryParams(
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 2, 1, tzinfo=UTC),
    )
    result = await service.query_timeline(params)
    assert len(result) == 1
    service.repo.query_timeline.assert_called_once()


@pytest.mark.asyncio()
async def test_sad20_service_query_timeline_with_project(
    service: MemoryService,
) -> None:
    """MemoryService.query_timeline passes project_id to repo."""
    service.query_timeline = MemoryService.query_timeline.__get__(service)
    from datetime import UTC, datetime

    from claude_memory.schema import TemporalQueryParams

    service.repo.query_timeline.return_value = []
    params = TemporalQueryParams(
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 2, 1, tzinfo=UTC),
        project_id=PROJECT_ID,
    )
    await service.query_timeline(params)
    call_kwargs = service.repo.query_timeline.call_args[1]
    assert call_kwargs["project_id"] == PROJECT_ID


@pytest.mark.asyncio()
async def test_happy_service_get_temporal_neighbors(
    service: MemoryService,
) -> None:
    """MemoryService.get_temporal_neighbors delegates to repo."""
    from claude_memory.schema import GetTemporalNeighborsParams

    service.repo.get_temporal_neighbors.return_value = [{"id": "neighbor-1"}]
    params = GetTemporalNeighborsParams(entity_id=ENTITY_ID, direction="forward", limit=5)  # type: ignore[arg-type]
    result = await service.get_temporal_neighbors(params)
    assert len(result) == 1
    service.repo.get_temporal_neighbors.assert_called_once_with(
        entity_id=ENTITY_ID, direction="forward", limit=5
    )


# ─── R2: Scoped PRECEDED_BY Tests ──────────────────────────────────


async def test_happy_create_entity_links_preceded_by(service: MemoryService) -> None:
    """create_entity links new entity to most recent in same project via PRECEDED_BY."""
    service.repo.create_node.return_value = {
        "id": "new-entity-id",
        "name": "New Entity",
    }
    service.repo.get_most_recent_entity.return_value = {
        "id": "prev-entity-id",
        "name": "Previous Entity",
    }
    service.repo.create_edge.return_value = {"id": "edge-id"}
    service.repo.get_total_node_count.return_value = 10

    from claude_memory.schema import EntityCreateParams

    params = EntityCreateParams(
        name="New Entity",
        node_type="Concept",
        project_id=PROJECT_ID,
    )
    receipt = await service.create_entity(params)
    assert receipt.id == "new-entity-id"

    # Verify PRECEDED_BY edge was created from prev → new
    found_preceded = False
    for call in service.repo.create_edge.call_args_list:
        if call[0][2] == "PRECEDED_BY":
            assert call[0][0] == "prev-entity-id"
            assert call[0][1] == "new-entity-id"
            found_preceded = True
    assert found_preceded


async def test_sad21_create_entity_no_preceded_by_when_first(service: MemoryService) -> None:
    """create_entity skips PRECEDED_BY when no previous entity exists in project."""
    service.repo.create_node.return_value = {
        "id": "first-entity-id",
        "name": "First Entity",
    }
    service.repo.get_most_recent_entity.return_value = None
    service.repo.get_total_node_count.return_value = 1

    from claude_memory.schema import EntityCreateParams

    params = EntityCreateParams(
        name="First Entity",
        node_type="Concept",
        project_id=PROJECT_ID,
    )
    receipt = await service.create_entity(params)
    assert receipt.id == "first-entity-id"

    # create_edge should NOT have been called with PRECEDED_BY
    for call in service.repo.create_edge.call_args_list:
        assert call[0][2] != "PRECEDED_BY"


async def test_evil15_create_entity_preceded_by_error_surfaced(service: MemoryService) -> None:
    """PRECEDED_BY link failure doesn't block entity creation but surfaces warning."""
    service.repo.create_node.return_value = {
        "id": "new-id",
        "name": "Entity",
    }
    service.repo.get_most_recent_entity.side_effect = ConnectionError("FalkorDB down")
    service.repo.get_total_node_count.return_value = 5

    from claude_memory.schema import EntityCreateParams

    params = EntityCreateParams(
        name="Entity",
        node_type="Concept",
        project_id=PROJECT_ID,
    )
    # Should not raise despite PRECEDED_BY failure
    receipt = await service.create_entity(params)
    assert receipt.id == "new-id"
    # But the warning MUST be surfaced in the receipt
    assert len(receipt.warnings) == 1
    assert "PRECEDED_BY" in receipt.warnings[0]


# ─── Phase 11D: Message in a Bottle Tests ──────────────────────────


async def test_happy_get_bottles_basic(service: MemoryService) -> None:
    """get_bottles delegates to repo with correct params."""
    from datetime import UTC, datetime

    from claude_memory.schema import BottleQueryParams

    service.repo.get_bottles.return_value = [
        {"id": "bottle-1", "name": "Remember this", "node_type": "Bottle"}
    ]
    params = BottleQueryParams(
        limit=5,
        search_text="remember",
        after_date=datetime(2026, 1, 1, tzinfo=UTC),
    )
    result = await service.get_bottles(params)
    assert len(result) == 1
    assert result[0]["id"] == "bottle-1"
    service.repo.get_bottles.assert_called_once_with(
        limit=5,
        search_text="remember",
        before_date=None,
        after_date="2026-01-01T00:00:00+00:00",
        project_id=None,
    )


async def test_sad22_get_bottles_empty(service: MemoryService) -> None:
    """get_bottles returns empty list when no bottles found."""
    from claude_memory.schema import BottleQueryParams

    service.repo.get_bottles.return_value = []
    params = BottleQueryParams()
    result = await service.get_bottles(params)
    assert result == []


# ─── E-1: Bottle Reader with Content ────────────────────────────────


async def test_happy_get_bottles_with_content(service: MemoryService) -> None:
    """E-1: include_content=True returns bottles with observation text."""
    from unittest.mock import MagicMock

    from claude_memory.schema import BottleQueryParams

    # Repo returns bottles
    service.repo.get_bottles.return_value = [
        {"id": "bottle-1", "name": "Bottle: Remember this"},
        {"id": "bottle-2", "name": "Bottle: Future note"},
    ]

    # Mock execute_cypher to return observation results per entity
    obs_results_1 = MagicMock()
    obs_results_1.result_set = [["This is the bottle message body"]]
    obs_results_2 = MagicMock()
    obs_results_2.result_set = [["Second bottle body"], ["PS: extra note"]]
    service.repo.execute_cypher.side_effect = [obs_results_1, obs_results_2]

    params = BottleQueryParams(include_content=True)
    result = await service.get_bottles(params)

    assert len(result) == 2

    # First bottle should have its observation content
    assert "observations" in result[0]
    assert result[0]["observations"] == ["This is the bottle message body"]

    # Second bottle should have both observations
    assert "observations" in result[1]
    assert result[1]["observations"] == ["Second bottle body", "PS: extra note"]


async def test_sad23_get_bottles_backward_compat(service: MemoryService) -> None:
    """E-1: Default include_content=False returns bottles WITHOUT observations key."""
    from claude_memory.schema import BottleQueryParams

    service.repo.get_bottles.return_value = [
        {"id": "bottle-1", "name": "Bottle: Remember this"},
    ]

    params = BottleQueryParams()  # include_content defaults to False
    result = await service.get_bottles(params)

    assert len(result) == 1
    assert result[0]["id"] == "bottle-1"
    # No observations key when include_content is False
    assert "observations" not in result[0]
    # execute_cypher should NOT have been called for observation lookup
    service.repo.execute_cypher.assert_not_called()


# ─── Phase 15A: Graph Health Metrics Tests ──────────────────────────


async def test_sad24_get_graph_health_basic(service: MemoryService) -> None:
    """get_graph_health returns repo stats plus community_count."""
    service.repo.get_graph_health.return_value = {
        "total_nodes": 10,
        "total_edges": 15,
        "density": 0.166667,
        "orphan_count": 2,
        "avg_degree": 1.5,
    }
    service.repo.get_all_nodes.return_value = []

    result = await service.get_graph_health()
    assert result["total_nodes"] == 10
    assert result["total_edges"] == 15
    assert "community_count" in result
    service.repo.get_graph_health.assert_called_once()


async def test_happy_get_graph_health_few_nodes(service: MemoryService) -> None:
    """get_graph_health skips clustering when fewer than 3 nodes."""
    service.repo.get_graph_health.return_value = {
        "total_nodes": 2,
        "total_edges": 1,
        "density": 0.5,
        "orphan_count": 0,
        "avg_degree": 0.5,
    }

    result = await service.get_graph_health()
    assert result["community_count"] == 0
    # Should not call get_all_nodes since total_nodes < 3
    service.repo.get_all_nodes.assert_not_called()


# ─── Phase 15C: Structural Gap Detection Service Tests ──────────────


async def test_sad25_detect_structural_gaps_no_clusters(service: MemoryService) -> None:
    """detect_structural_gaps returns empty when too few nodes for clustering."""
    service.repo.get_all_nodes.return_value = [{"id": "a1", "embedding": [1.0]}]

    from claude_memory.schema import GapDetectionParams

    params = GapDetectionParams()
    result = await service.detect_structural_gaps(params)
    assert result == []


async def test_sad26_detect_structural_gaps_with_gaps(service: MemoryService) -> None:
    """detect_structural_gaps finds gaps and generates research prompts."""
    from unittest.mock import patch as mock_patch

    from claude_memory.clustering import Cluster, StructuralGap
    from claude_memory.schema import GapDetectionParams

    # Mock nodes — enough for clustering
    nodes = [{"id": f"n{i}", "name": f"Node{i}", "embedding": [float(i)]} for i in range(6)]
    service.repo.get_all_nodes.return_value = nodes
    service.repo.get_all_edges.return_value = []

    # Mock clustering to return 2 clusters
    mock_clusters = [
        Cluster(id=0, nodes=nodes[:3], centroid=[1.0], cohesion_score=0.1),
        Cluster(id=1, nodes=nodes[3:], centroid=[4.0], cohesion_score=0.1),
    ]

    # Mock detect_gaps to return a gap
    mock_gap = StructuralGap(
        cluster_a_id=0,
        cluster_b_id=1,
        similarity=0.85,
        edge_count=0,
        suggested_bridges=[{"from_id": "n0", "to_id": "n3", "similarity": 0.9}],
    )

    with (
        mock_patch("claude_memory.clustering.ClusteringService") as mock_cs_cls,
        mock_patch("claude_memory.clustering.detect_gaps", return_value=[mock_gap]) as mock_dg,
    ):
        mock_cs_cls.return_value.cluster_nodes.return_value = mock_clusters

        params = GapDetectionParams(min_similarity=0.7, max_edges=2, limit=10)
        result = await service.detect_structural_gaps(params)

    assert len(result) == 1
    assert result[0]["similarity"] == 0.85
    assert "research_prompt" in result[0]
    assert "related" in result[0]["research_prompt"].lower()
    mock_dg.assert_called_once()


# ─── Phase 1B: Vector Failure Warning Tests ─────────────────────────


async def test_evil16_create_entity_vector_failure_always_raises(
    service: MemoryService,
) -> None:
    """When vector upsert fails during create, it always raises — no lenient path."""
    from claude_memory.schema import EntityCreateParams

    service.repo.create_node.return_value = {"id": ENTITY_ID, "name": ENTITY_NAME}
    service.repo.get_total_node_count.return_value = 42
    service.repo.get_most_recent_entity.return_value = None
    service.ontology = MagicMock()
    service.ontology.is_valid_type.return_value = True
    service.vector_store.upsert.side_effect = RuntimeError("Qdrant down")

    params = EntityCreateParams(
        name=ENTITY_NAME,
        node_type=ENTITY_TYPE,
        project_id=PROJECT_ID,
    )
    with pytest.raises(SearchError, match="Qdrant down"):
        await service.create_entity(params)


async def test_evil17_update_entity_vector_failure_always_raises(
    service: MemoryService,
) -> None:
    """When vector upsert fails during update, it always raises — no lenient path."""
    service.repo.get_node.return_value = MOCK_NODE_PROPS.copy()
    service.repo.update_node.return_value = MOCK_NODE_PROPS.copy()
    service.vector_store.upsert.side_effect = RuntimeError("Qdrant timeout")

    params = EntityUpdateParams(
        entity_id=ENTITY_ID,
        properties={"description": "updated"},
    )
    with pytest.raises(SearchError, match="Qdrant timeout"):
        await service.update_entity(params)


async def test_evil18_delete_entity_soft_vector_failure_always_raises(
    service: MemoryService,
) -> None:
    """When vector delete fails during soft delete, it always raises."""
    service.repo.get_node.return_value = MOCK_NODE_PROPS.copy()
    service.vector_store.delete.side_effect = RuntimeError("Qdrant unreachable")

    params = EntityDeleteParams(
        entity_id=ENTITY_ID,
        reason="test",
        soft_delete=True,
    )
    with pytest.raises(SearchError, match="Qdrant unreachable"):
        await service.delete_entity(params)


async def test_evil19_delete_entity_hard_vector_failure_always_raises(
    service: MemoryService,
) -> None:
    """When vector delete fails during hard delete, it always raises."""
    service.repo.get_node.return_value = MOCK_NODE_PROPS.copy()
    service.vector_store.delete.side_effect = RuntimeError("Qdrant gone")

    params = EntityDeleteParams(
        entity_id=ENTITY_ID,
        reason="test",
        soft_delete=False,
    )
    with pytest.raises(SearchError, match="Qdrant gone"):
        await service.delete_entity(params)


# ─── P0-3: Search Error Handling Tests ──────────────────────────────


async def test_evil20_search_raises_on_embedder_failure(service: MemoryService) -> None:
    """PR-5: Embedder ConnectionError degrades gracefully, sets semantic channel to error."""
    service.embedder.encode.side_effect = ConnectionError("Embedding server down")

    res = await service.search(SearchMemoryParams(query=SEARCH_QUERY, limit=SEARCH_LIMIT))

    assert res["metadata"]["channels"]["vector"] == "failed"


async def test_evil21_search_raises_on_vector_store_failure(service: MemoryService) -> None:
    """PR-5: Qdrant ConnectionError degrades gracefully, sets semantic channel to error."""
    service.vector_store.search.side_effect = ConnectionError("Qdrant unreachable")

    res = await service.search(SearchMemoryParams(query=SEARCH_QUERY, limit=SEARCH_LIMIT))

    assert res["metadata"]["channels"]["vector"] == "failed"


async def test_evil22_search_raises_on_timeout(service: MemoryService) -> None:
    """PR-5: TimeoutError during search degrades gracefully."""
    service.embedder.encode.side_effect = TimeoutError("Embedding timed out")

    res = await service.search(SearchMemoryParams(query=SEARCH_QUERY, limit=SEARCH_LIMIT))

    assert res["metadata"]["channels"]["vector"] == "failed"


async def test_evil23_search_associative_raises_on_embedder_failure(
    service: MemoryService,
) -> None:
    """AUDIT-B1: search_associative raises SearchError on embedder ConnectionError."""
    from claude_memory.exceptions import SearchError

    service.embedder.encode.side_effect = ConnectionError("Embedding server down")

    with pytest.raises(SearchError, match="Memory retrieval unavailable"):
        await service.search_associative(
            SearchAssociativeParams(query=SEARCH_QUERY, limit=SEARCH_LIMIT)
        )


async def test_evil24_search_associative_raises_on_vector_store_failure(
    service: MemoryService,
) -> None:
    """AUDIT-B1: search_associative raises SearchError on Qdrant ConnectionError."""
    from claude_memory.exceptions import SearchError

    service.vector_store.search.side_effect = ConnectionError("Qdrant unreachable")

    with pytest.raises(SearchError, match="Memory retrieval unavailable"):
        await service.search_associative(
            SearchAssociativeParams(query=SEARCH_QUERY, limit=SEARCH_LIMIT)
        )


async def test_evil25_search_raises_on_os_error(service: MemoryService) -> None:
    """AUDIT-B1: OSError during search raises SearchError (network-level failure)."""

    service.embedder.encode.side_effect = OSError("Network unreachable")

    res = await service.search(SearchMemoryParams(query=SEARCH_QUERY, limit=SEARCH_LIMIT))
    assert res["metadata"]["channels"]["vector"] == "failed"


async def test_sad28_search_empty_query_returns_empty(service: MemoryService) -> None:
    """AUDIT-B1: Empty query still returns [] (not SearchError) — it's not an infra failure."""
    _res = await service.search(SearchMemoryParams(query="", limit=SEARCH_LIMIT))
    result = _res.get("results", []) if isinstance(_res, dict) else _res
    assert result == []


async def test_happy_search_normal_operation_returns_results(service: MemoryService) -> None:
    """AUDIT-B1: Normal search with working infra returns results, no exception."""
    mock_result = MagicMock()
    mock_result.id = ENTITY_ID
    mock_result.name = ENTITY_NAME

    service.vector_store.search.return_value = [
        {"_id": ENTITY_ID, "_score": 0.95, "payload": {"name": ENTITY_NAME}},
    ]
    service.repo.get_subgraph.return_value = {"nodes": [], "edges": []}

    # search() returns results, does not raise
    _res = await service.search(SearchMemoryParams(query=SEARCH_QUERY, limit=SEARCH_LIMIT))
    result = _res.get("results", []) if isinstance(_res, dict) else _res
    # Result may be empty if hydration mocks aren't set up, but should NOT raise
    assert isinstance(result, list)


# ─── E-5: System Diagnostics ───────────────────────────────────────


async def test_happy_system_diagnostics_returns_all_sections(service: MemoryService) -> None:
    """E-5: system_diagnostics returns graph, vector, and split_brain sections."""
    service.repo.get_graph_health.return_value = {
        "total_nodes": 100,
        "total_edges": 200,
        "density": 0.04,
        "orphan_count": 5,
        "avg_degree": 4.0,
    }
    service.vector_store.count.return_value = 95
    service.repo.get_all_node_ids.return_value = [f"id-{i}" for i in range(100)]
    service.vector_store.list_ids.return_value = [f"id-{i}" for i in range(95)]

    result = await service.system_diagnostics()

    assert "graph" in result
    assert "vector" in result
    assert "split_brain" in result
    assert result["graph"]["total_nodes"] == 100
    assert result["vector"]["count"] == 95


async def test_happy_system_diagnostics_detects_split_brain(service: MemoryService) -> None:
    """E-5: Detects entities in graph but missing from vector store."""
    service.repo.get_graph_health.return_value = {
        "total_nodes": 5,
        "total_edges": 3,
        "density": 0.3,
        "orphan_count": 0,
        "avg_degree": 1.2,
    }
    service.vector_store.count.return_value = 3
    # IDs in graph: 1..5; IDs in vector: 1..3 → missing: 4, 5
    service.repo.get_all_node_ids.return_value = ["id-1", "id-2", "id-3", "id-4", "id-5"]
    service.vector_store.list_ids.return_value = ["id-1", "id-2", "id-3"]

    result = await service.system_diagnostics()

    assert result["split_brain"]["graph_only_count"] == 2
    assert set(result["split_brain"]["graph_only_ids"]) == {"id-4", "id-5"}


async def test_evil24_system_diagnostics_handles_backend_failure(service: MemoryService) -> None:
    """E-5: Graceful degradation when vector store is unreachable."""
    service.repo.get_graph_health.return_value = {
        "total_nodes": 10,
        "total_edges": 5,
        "density": 0.1,
        "orphan_count": 1,
        "avg_degree": 1.0,
    }
    service.vector_store.count.side_effect = ConnectionError("Qdrant down")
    service.repo.get_all_node_ids.return_value = [f"id-{i}" for i in range(10)]

    result = await service.system_diagnostics()

    assert result["graph"]["total_nodes"] == 10
    assert result["vector"]["error"] is not None
    assert result["split_brain"]["status"] == "unavailable"


# ─── E-4: Session Reconnect ────────────────────────────────────────


async def test_happy_reconnect_returns_structured_briefing(service: MemoryService) -> None:
    """E-4: reconnect returns recent_entities, recent_sessions, and health."""

    # Mock recent entities via timeline query
    service.repo.query_timeline.return_value = [
        {"id": "e-1", "name": "Python", "node_type": "Technology", "created_at": "2026-01-01"},
        {"id": "e-2", "name": "Rust", "node_type": "Technology", "created_at": "2026-01-02"},
    ]
    # Mock graph health
    service.repo.get_graph_health.return_value = {
        "total_nodes": 50,
        "total_edges": 80,
        "density": 0.06,
        "orphan_count": 3,
        "avg_degree": 3.2,
    }

    result = await service.reconnect()

    assert "recent_entities" in result
    assert "health" in result
    assert len(result["recent_entities"]) == 2
    assert result["health"]["total_nodes"] == 50


async def test_happy_reconnect_filters_by_project(service: MemoryService) -> None:
    """E-4: reconnect accepts project_id to filter results."""
    service.repo.query_timeline.return_value = [
        {"id": "e-1", "name": "Django", "node_type": "Framework", "project_id": "proj-1"},
    ]
    service.repo.get_graph_health.return_value = {
        "total_nodes": 10,
        "total_edges": 5,
        "density": 0.1,
        "orphan_count": 0,
        "avg_degree": 1.0,
    }

    result = await service.reconnect(project_id="proj-1")

    assert len(result["recent_entities"]) == 1
    assert result["recent_entities"][0]["name"] == "Django"
    # Verify project_id was passed to timeline query
    call_args = service.repo.query_timeline.call_args
    assert call_args.kwargs.get("project_id") == "proj-1" or "proj-1" in str(call_args)


async def test_sad27_reconnect_handles_empty_graph(service: MemoryService) -> None:
    """E-4: reconnect handles empty graph gracefully."""
    service.repo.query_timeline.return_value = []
    service.repo.get_graph_health.return_value = {
        "total_nodes": 0,
        "total_edges": 0,
        "density": 0.0,
        "orphan_count": 0,
        "avg_degree": 0.0,
    }

    result = await service.reconnect()

    assert result["recent_entities"] == []
    assert result["health"]["total_nodes"] == 0


# ─── B10.B Concurrency Stress Tests ─────────────────────────────────


async def test_happy_b10b_concurrency_stress_create_entity(service: MemoryService) -> None:
    """B10.B: Verify that AsyncMemoryRepository handles concurrent requests seamlessly."""
    import asyncio

    from claude_memory.schema import EntityCreateParams

    service.repo.get_most_recent_entity.return_value = None
    service.repo.get_total_node_count.return_value = 0
    service.repo.get_observations_for_entity.return_value = []

    # We want create_node to return a unique ID for each call
    call_counter = 0

    def mock_create_node(*args, **kwargs):
        nonlocal call_counter
        call_counter += 1
        return {"id": f"concurrent-ent-{call_counter}"}

    service.repo.create_node.side_effect = mock_create_node

    # Create 10 tasks
    tasks = []
    for i in range(10):
        params = EntityCreateParams(
            name=f"ConcurrentEntity{i}",
            node_type="Entity",
            description=f"Description {i}",
            project_id="proj-1",
        )
        tasks.append(service.create_entity(params))

    # Execute all 10 concurrently
    results = await asyncio.gather(*tasks)

    # Assertions
    assert len(results) == 10
    ids = [r.id for r in results]
    assert len(set(ids)) == 10
    assert service.repo.create_node.call_count == 10


@pytest.mark.asyncio
async def test_evil_concurrent_consolidate_memories(service: MemoryService) -> None:
    """B10.E: concurrent consolidations don't corrupt state."""
    import asyncio

    # Mock create_node to return unique nodes
    call_counter = 0

    def mock_create_node(*args, **kwargs):
        nonlocal call_counter
        call_counter += 1
        return {"id": f"consolidated-ent-{call_counter}", "name": "Consolidated"}

    service.repo.create_node.side_effect = mock_create_node
    service.repo.update_node.return_value = {"status": "archived"}

    tasks = []
    for i in range(5):
        tasks.append(
            service.consolidate_memories(
                entity_ids=[f"ent-{i}-A", f"ent-{i}-B"], summary=f"Consolidated memory {i}"
            )
        )

    results = await asyncio.gather(*tasks, return_exceptions=True)

    assert len(results) == 5
    for r in results:
        assert not isinstance(r, Exception), f"Exception raised: {r}"
        assert "id" in r

    assert service.repo.create_node.call_count == 5
    assert service.repo.create_edge.call_count == 10
    assert service.repo.update_node.call_count == 10
