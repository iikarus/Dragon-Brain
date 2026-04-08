"""Tests for the MCP server module (server.py).

Tests all MCP tool wrappers to ensure they correctly:
1. Build params from arguments
2. Delegate to the MemoryService with correct params
3. Return service results
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ─── Test Constants ─────────────────────────────────────────────────
# All test data is defined here — zero magic values in test bodies.

ENTITY_NAME = "Python"
ENTITY_TYPE = "Language"
PROJECT_ID = "project-alpha"
ENTITY_ID = "entity-001"
ENTITY_PROPERTIES = {"paradigm": "multi-paradigm"}
CERTAINTY_CONFIRMED = "confirmed"
CERTAINTY_SPECULATIVE = "speculative"
EVIDENCE_ITEM = "official-docs"

RELATIONSHIP_FROM = "entity-001"
RELATIONSHIP_TO = "entity-002"
RELATIONSHIP_TYPE = "RELATED_TO"
RELATIONSHIP_ALT_TYPE = "DEPENDS_ON"
RELATIONSHIP_ID = "rel-001"
RELATIONSHIP_CONFIDENCE = 0.95
RELATIONSHIP_CONFIDENCE_DEFAULT = 1.0
RELATIONSHIP_PROPS = {"weight": 0.9}
DELETE_REASON = "no-longer-relevant"

OBSERVATION_CONTENT = "Observed pattern in codebase"
SESSION_FOCUS = "architecture-review"
SESSION_ID = "session-001"
SESSION_SUMMARY = "Reviewed and refactored core module"
SESSION_OUTCOME = "fixed-race-condition"
UPDATE_REASON = "version-bump"
UPDATE_PROPS = {"version": "3.12"}

BREAKTHROUGH_NAME = "eureka-moment"
BREAKTHROUGH_MOMENT = "2024-06-15T14:30:00Z"
BREAKTHROUGH_ANALOGY = "like-water-flow"
BREAKTHROUGH_CONCEPT = "concept-async-patterns"

GRAPH_DEPTH = 2
GRAPH_LIMIT = 5
SEARCH_LIMIT = 5
PRUNE_DAYS = 7
SEARCH_QUERY = "async patterns in Python"
TIME_QUERY_AS_OF = "2024-01-01T00:00:00Z"

SSE_PORT = 9000
SSE_PORT_STR = "9000"
TRANSPORT_SSE = "sse"
TRANSPORT_STDIO = "stdio"

MEMORY_TYPE_NAME = "Recipe"
MEMORY_TYPE_DESC = "A structured culinary recipe"
MEMORY_TYPE_REQUIRED_PROP = "ingredients"


# ─── Module Import (with patches) ──────────────────────────────────
# server.py eagerly instantiates services at module level,
# so we must patch dependencies before import.

with patch.dict(os.environ, {"EMBEDDING_API_URL": "http://mock-embedding-api"}):
    with patch("claude_memory.embedding.EmbeddingService"):
        with patch("claude_memory.repository.FalkorDB"):
            with patch("claude_memory.lock_manager.redis.Redis"):
                from claude_memory import server, tools_extra

# ─── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _mock_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the global `service` with an AsyncMock for all tests."""
    mock_svc = AsyncMock()
    mock_svc.create_entity = AsyncMock(return_value=MagicMock())
    mock_svc.update_entity = AsyncMock(return_value={"status": "updated"})
    mock_svc.delete_entity = AsyncMock(return_value={"status": "deleted"})
    mock_svc.create_relationship = AsyncMock(return_value={"status": "created"})
    mock_svc.delete_relationship = AsyncMock(return_value={"status": "deleted"})
    mock_svc.add_observation = AsyncMock(return_value={"status": "added"})
    mock_svc.start_session = AsyncMock(return_value={"session_id": SESSION_ID})
    mock_svc.end_session = AsyncMock(return_value={"status": "ended"})
    mock_svc.record_breakthrough = AsyncMock(return_value={"status": "recorded"})
    mock_svc.get_neighbors = AsyncMock(return_value=[{"id": ENTITY_ID}])
    mock_svc.traverse_path = AsyncMock(return_value=[{"id": ENTITY_ID}])
    mock_svc.find_cross_domain_patterns = AsyncMock(return_value=[])
    mock_svc.get_evolution = AsyncMock(return_value=[])
    mock_svc.point_in_time_query = AsyncMock(return_value=[])
    mock_svc.archive_entity = AsyncMock(return_value={"status": "archived"})
    mock_svc.prune_stale = AsyncMock(return_value={"pruned": PRUNE_DAYS})
    mock_svc.search = AsyncMock(return_value=[])
    mock_svc.create_memory_type = MagicMock(return_value={"name": MEMORY_TYPE_NAME})
    monkeypatch.setattr(server, "service", mock_svc)
    monkeypatch.setattr(tools_extra, "_service", mock_svc)


@pytest.fixture()
def _mock_librarian(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_lib = AsyncMock()
    mock_lib.run_cycle = AsyncMock(
        return_value={
            "clusters_found": 3,
            "consolidations_created": 0,
            "deleted_stale": 0,
            "gaps_detected": 0,
            "gap_reports_stored": 0,
            "errors": [],
        }
    )
    monkeypatch.setattr(server, "librarian", mock_lib)
    monkeypatch.setattr(tools_extra, "_librarian", mock_lib)


# ─── Entity Tool Tests ──────────────────────────────────────────────


async def test_happy_create_entity_with_all_params() -> None:
    result = await server.create_entity(
        name=ENTITY_NAME,
        node_type=ENTITY_TYPE,
        project_id=PROJECT_ID,
        properties=ENTITY_PROPERTIES,
        certainty=CERTAINTY_CONFIRMED,
        evidence=[EVIDENCE_ITEM],
    )
    server.service.create_entity.assert_awaited_once()
    assert result is not None


async def test_sad1_create_entity_defaults() -> None:
    """Verify None defaults become empty list/dict."""
    await server.create_entity(name=ENTITY_NAME, node_type=ENTITY_TYPE, project_id=PROJECT_ID)
    params = server.service.create_entity.call_args[0][0]
    assert params.evidence == []
    assert params.properties == {}


async def test_happy_update_entity() -> None:
    result = await server.update_entity(
        entity_id=ENTITY_ID, properties=UPDATE_PROPS, reason=UPDATE_REASON
    )
    server.service.update_entity.assert_awaited_once()
    assert result == {"status": "updated"}


async def test_happy_delete_entity() -> None:
    result = await server.delete_entity(entity_id=ENTITY_ID, reason=DELETE_REASON, soft_delete=True)
    server.service.delete_entity.assert_awaited_once()
    assert result == {"status": "deleted"}


# ─── Relationship Tool Tests ────────────────────────────────────────


async def test_happy_create_relationship_with_all_params() -> None:
    result = await server.create_relationship(
        from_entity=RELATIONSHIP_FROM,
        to_entity=RELATIONSHIP_TO,
        relationship_type=RELATIONSHIP_TYPE,
        properties=RELATIONSHIP_PROPS,
        confidence=RELATIONSHIP_CONFIDENCE,
    )
    server.service.create_relationship.assert_awaited_once()
    assert result == {"status": "created"}


async def test_sad2_create_relationship_defaults() -> None:
    await server.create_relationship(
        from_entity=RELATIONSHIP_FROM,
        to_entity=RELATIONSHIP_TO,
        relationship_type=RELATIONSHIP_ALT_TYPE,
    )
    params = server.service.create_relationship.call_args[0][0]
    assert params.properties == {}
    assert params.confidence == RELATIONSHIP_CONFIDENCE_DEFAULT


async def test_happy_delete_relationship() -> None:
    result = await server.delete_relationship(relationship_id=RELATIONSHIP_ID, reason=DELETE_REASON)
    server.service.delete_relationship.assert_awaited_once()
    assert result == {"status": "deleted"}


# ─── Observation Tool Tests ─────────────────────────────────────────


async def test_happy_add_observation_with_evidence() -> None:
    result = await server.add_observation(
        entity_id=ENTITY_ID,
        content=OBSERVATION_CONTENT,
        certainty=CERTAINTY_SPECULATIVE,
        evidence=[EVIDENCE_ITEM],
    )
    server.service.add_observation.assert_awaited_once()
    assert result == {"status": "added"}


async def test_sad3_add_observation_defaults() -> None:
    await server.add_observation(entity_id=ENTITY_ID, content=OBSERVATION_CONTENT)
    params = server.service.add_observation.call_args[0][0]
    assert params.evidence == []


# ─── Session Tool Tests ─────────────────────────────────────────────


async def test_happy_start_session() -> None:
    result = await server.start_session(project_id=PROJECT_ID, focus=SESSION_FOCUS)
    server.service.start_session.assert_awaited_once()
    assert result == {"session_id": SESSION_ID}


async def test_happy_end_session_with_outcomes() -> None:
    result = await server.end_session(
        session_id=SESSION_ID, summary=SESSION_SUMMARY, outcomes=[SESSION_OUTCOME]
    )
    server.service.end_session.assert_awaited_once()
    assert result == {"status": "ended"}


async def test_sad4_end_session_defaults() -> None:
    await server.end_session(session_id=SESSION_ID, summary=SESSION_SUMMARY)
    params = server.service.end_session.call_args[0][0]
    assert params.outcomes == []


# ─── Breakthrough Tool Tests ────────────────────────────────────────


async def test_happy_record_breakthrough_with_all_params() -> None:
    result = await server.record_breakthrough(
        name=BREAKTHROUGH_NAME,
        moment=BREAKTHROUGH_MOMENT,
        session_id=SESSION_ID,
        analogy_used=BREAKTHROUGH_ANALOGY,
        concepts_unlocked=[BREAKTHROUGH_CONCEPT],
    )
    server.service.record_breakthrough.assert_awaited_once()
    assert result == {"status": "recorded"}


async def test_sad5_record_breakthrough_defaults() -> None:
    await server.record_breakthrough(
        name=BREAKTHROUGH_NAME, moment=BREAKTHROUGH_MOMENT, session_id=SESSION_ID
    )
    params = server.service.record_breakthrough.call_args[0][0]
    assert params.concepts_unlocked == []


# ─── Graph Traversal Tool Tests ─────────────────────────────────────


async def test_happy_get_neighbors() -> None:
    result = await server.get_neighbors(entity_id=ENTITY_ID, depth=GRAPH_DEPTH, limit=GRAPH_LIMIT)
    server.service.get_neighbors.assert_awaited_once_with(ENTITY_ID, GRAPH_DEPTH, GRAPH_LIMIT, 0)
    assert result == [{"id": ENTITY_ID}]


async def test_happy_traverse_path() -> None:
    result = await server.traverse_path(from_id=RELATIONSHIP_FROM, to_id=RELATIONSHIP_TO)
    server.service.traverse_path.assert_awaited_once_with(RELATIONSHIP_FROM, RELATIONSHIP_TO)
    assert result == [{"id": ENTITY_ID}]


async def test_sad6_find_cross_domain_patterns() -> None:
    result = await server.find_cross_domain_patterns(entity_id=ENTITY_ID, limit=GRAPH_LIMIT)
    server.service.find_cross_domain_patterns.assert_awaited_once_with(ENTITY_ID, GRAPH_LIMIT)
    assert result == []


# ─── Temporal Tool Tests ────────────────────────────────────────────


async def test_sad7_get_evolution() -> None:
    result = await server.get_evolution(entity_id=ENTITY_ID)
    server.service.get_evolution.assert_awaited_once_with(ENTITY_ID)
    assert result == []


async def test_sad8_point_in_time_query() -> None:
    result = await server.point_in_time_query(query_text=SEARCH_QUERY, as_of=TIME_QUERY_AS_OF)
    server.service.point_in_time_query.assert_awaited_once_with(SEARCH_QUERY, TIME_QUERY_AS_OF)
    assert result == []


async def test_happy_archive_entity() -> None:
    result = await server.archive_entity(entity_id=ENTITY_ID)
    server.service.archive_entity.assert_awaited_once_with(ENTITY_ID)
    assert result == {"status": "archived"}


async def test_sad9_prune_stale() -> None:
    result = await server.prune_stale(days=PRUNE_DAYS)
    server.service.prune_stale.assert_awaited_once_with(PRUNE_DAYS)
    assert result == {"pruned": PRUNE_DAYS}


# ─── Search Tool Tests ──────────────────────────────────────────────


async def test_sad10_search_memory_no_results() -> None:
    result = await server.search_memory(query=SEARCH_QUERY)
    assert result == "No results found."


async def test_happy_search_memory_with_results() -> None:
    mock_result = MagicMock()
    mock_result.model_dump.return_value = {"id": ENTITY_ID, "name": ENTITY_NAME}
    server.service.search = AsyncMock(return_value=[mock_result])
    result = await server.search_memory(
        query=SEARCH_QUERY, project_id=PROJECT_ID, limit=SEARCH_LIMIT
    )
    assert result == [{"id": ENTITY_ID, "name": ENTITY_NAME}]


# ─── Librarian Tool Tests ───────────────────────────────────────────


@pytest.mark.usefixtures("_mock_librarian")
async def test_happy_run_librarian_cycle() -> None:
    result = await server.run_librarian_cycle()
    assert result["clusters_found"] == 3


# ─── Ontology Tool Tests ────────────────────────────────────────────


async def test_happy_create_memory_type_with_required_props() -> None:
    result = await server.create_memory_type(
        name=MEMORY_TYPE_NAME,
        description=MEMORY_TYPE_DESC,
        required_properties=[MEMORY_TYPE_REQUIRED_PROP],
    )
    server.service.create_memory_type.assert_called_once()
    assert result == {"name": MEMORY_TYPE_NAME}


async def test_sad11_create_memory_type_defaults() -> None:
    await server.create_memory_type(name=MEMORY_TYPE_NAME, description=MEMORY_TYPE_DESC)
    call_args = server.service.create_memory_type.call_args[0]
    assert call_args[2] == []  # required_properties defaults to []


# ─── Main Entry Point Tests ─────────────────────────────────────────


def test_happy_main_stdio_transport() -> None:
    with patch("anyio.run") as mock_anyio_run:
        with patch.dict(os.environ, {"MCP_TRANSPORT": TRANSPORT_STDIO}):
            server.main()
            mock_anyio_run.assert_called_once_with(server._run_stdio_buffered)


def test_happy_main_sse_transport() -> None:
    """SSE transport was removed in Phase 7. main() now always uses stdio.
    We verify that main() calls anyio.run() regardless of MCP_TRANSPORT env."""
    with patch("anyio.run") as mock_anyio_run:
        with patch.dict(os.environ, {"MCP_TRANSPORT": TRANSPORT_SSE}):
            server.main()
            mock_anyio_run.assert_called_once_with(server._run_stdio_buffered)
