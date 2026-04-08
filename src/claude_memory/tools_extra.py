"""Extra MCP tool handlers — search variants, temporal, librarian, health.

Functions are defined at module level so they can be imported by tests.
``configure()`` is called from ``server.py`` to bind the MCP app and
inject service references before any tool is invoked.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from claude_memory.schema import (
    BottleQueryParams,
    GapDetectionParams,
    TemporalQueryParams,
)
from claude_memory.timeout import MCP_OP_TIMEOUT, MCP_OP_TIMEOUT_SEARCH, timed_call

if TYPE_CHECKING:  # pragma: no cover
    from mcp.server.fastmcp import FastMCP

    from claude_memory.librarian import LibrarianAgent
    from claude_memory.tools import MemoryService

# Late-bound references, set by configure()
_service: MemoryService | None = None
_librarian: LibrarianAgent | None = None


def configure(mcp: FastMCP, service: MemoryService, librarian: LibrarianAgent) -> None:
    """Bind service dependencies and register handlers on the MCP app.

    Must be called once from ``server.py`` before any tool is invoked.
    """
    global _service, _librarian  # noqa: PLW0603
    _service = service
    _librarian = librarian

    mcp.tool()(search_associative)
    mcp.tool()(run_librarian_cycle)
    mcp.tool()(create_memory_type)
    mcp.tool()(query_timeline)
    mcp.tool()(get_temporal_neighbors)
    mcp.tool()(get_bottles)
    mcp.tool()(graph_health)
    mcp.tool()(find_knowledge_gaps)
    mcp.tool()(reconnect)
    mcp.tool()(system_diagnostics)
    mcp.tool()(list_orphans)
    mcp.tool()(semantic_radar)
    mcp.tool()(find_semantic_opportunities)


async def search_associative(  # noqa: PLR0913
    query: str,
    limit: int = 10,
    project_id: str | None = None,
    decay: float = 0.6,
    max_hops: int = 3,
    w_sim: float | None = None,
    w_act: float | None = None,
    w_sal: float | None = None,
    w_rec: float | None = None,
) -> list[dict[str, Any]]:
    """Associative search using spreading activation through the knowledge graph.

    Combines vector similarity with graph-based energy propagation for
    richer, context-aware retrieval.  Score weights default to env vars
    ``W_SIMILARITY``, ``W_ACTIVATION``, ``W_SALIENCE``, ``W_RECENCY``.
    """
    _t0 = time.monotonic()
    results = await timed_call("search_associative", _service.search_associative(  # type: ignore[union-attr]
        query,
        limit=limit,
        project_id=project_id,
        decay=decay,
        max_hops=max_hops,
        w_sim=w_sim,
        w_act=w_act,
        w_sal=w_sal,
        w_rec=w_rec,
    ), MCP_OP_TIMEOUT_SEARCH, dispatch_t0=_t0)
    if not results:
        return [{"message": "No results found."}]
    return [res.model_dump() for res in results]


async def run_librarian_cycle() -> dict[str, Any]:
    """Triggers the Librarian Agent to cluster and consolidate memories."""
    _t0 = time.monotonic()
    return await timed_call("run_librarian_cycle", _librarian.run_cycle(), MCP_OP_TIMEOUT, dispatch_t0=_t0)  # type: ignore[union-attr]


async def create_memory_type(
    name: str, description: str, required_properties: list[str] | None = None
) -> dict[str, Any]:
    """Registers a new memory type in the ontology.

    Args:
        name: Name of the new type (e.g. "Recipe")
        description: Description of what this type represents
        required_properties: List of property names that should always be present
    """
    if required_properties is None:
        required_properties = []
    return _service.create_memory_type(name, description, required_properties)  # type: ignore[union-attr]


async def query_timeline(
    start: str,
    end: str,
    limit: int = 20,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Query entities within a time window, ordered chronologically."""
    from datetime import datetime  # noqa: PLC0415

    params = TemporalQueryParams(
        start=datetime.fromisoformat(start),
        end=datetime.fromisoformat(end),
        limit=limit,
        project_id=project_id,
    )
    _t0 = time.monotonic()
    return await timed_call("query_timeline", _service.query_timeline(params), MCP_OP_TIMEOUT, dispatch_t0=_t0)  # type: ignore[union-attr]


async def get_temporal_neighbors(
    entity_id: str,
    direction: str = "both",
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Find entities connected by temporal edges (before/after/both)."""
    _t0 = time.monotonic()
    return await timed_call("get_temporal_neighbors", _service.get_temporal_neighbors(entity_id, direction, limit), MCP_OP_TIMEOUT, dispatch_t0=_t0)  # type: ignore[union-attr]


async def get_bottles(  # noqa: PLR0913
    limit: int = 10,
    search_text: str | None = None,
    before_date: str | None = None,
    after_date: str | None = None,
    project_id: str | None = None,
    include_content: bool = False,
) -> list[dict[str, Any]]:
    """Query 'Message in a Bottle' entities — timestamped notes to your future self."""
    from datetime import datetime as dt  # noqa: PLC0415

    params = BottleQueryParams(
        limit=limit,
        search_text=search_text,
        before_date=dt.fromisoformat(before_date) if before_date else None,
        after_date=dt.fromisoformat(after_date) if after_date else None,
        project_id=project_id,
        include_content=include_content,
    )
    _t0 = time.monotonic()
    return await timed_call("get_bottles", _service.get_bottles(params), MCP_OP_TIMEOUT, dispatch_t0=_t0)  # type: ignore[union-attr]


async def graph_health() -> dict[str, Any]:
    """Get graph health metrics: nodes, edges, density, orphans, communities, avg degree."""
    _t0 = time.monotonic()
    return await timed_call("graph_health", _service.get_graph_health(), MCP_OP_TIMEOUT, dispatch_t0=_t0)  # type: ignore[union-attr]


async def find_knowledge_gaps(
    min_similarity: float = 0.7,
    max_edges: int = 2,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Find structural gaps: clusters that are semantically similar but poorly connected."""
    params = GapDetectionParams(
        min_similarity=min_similarity,
        max_edges=max_edges,
        limit=limit,
    )
    _t0 = time.monotonic()
    return await timed_call("find_knowledge_gaps", _service.detect_structural_gaps(params), MCP_OP_TIMEOUT_SEARCH, dispatch_t0=_t0)  # type: ignore[union-attr]


async def reconnect(
    project_id: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Session reconnect — structured briefing for a returning agent.

    Returns recent entities (last 24h), graph health summary, and time window.
    """
    _t0 = time.monotonic()
    return await timed_call("reconnect", _service.reconnect(project_id=project_id, limit=limit), MCP_OP_TIMEOUT, dispatch_t0=_t0)  # type: ignore[union-attr]


async def system_diagnostics() -> dict[str, Any]:
    """Unified system diagnostics — graph stats, vector stats, and split-brain check."""
    _t0 = time.monotonic()
    return await timed_call("system_diagnostics", _service.system_diagnostics(), MCP_OP_TIMEOUT, dispatch_t0=_t0)  # type: ignore[union-attr]


async def list_orphans(limit: int = 50) -> list[dict[str, Any]]:
    """List graph nodes with zero relationships (orphans).

    Returns id, name, node_type, project_id, focus, labels, and
    created_at for each orphan so the caller can decide whether to
    reconnect or delete them.

    Args:
        limit: Maximum nodes to return (default 50, safety cap).
    """
    _t0 = time.monotonic()
    return await timed_call("list_orphans", _service.list_orphans(limit=limit), MCP_OP_TIMEOUT, dispatch_t0=_t0)  # type: ignore[union-attr]


async def semantic_radar(
    entity_id: str,
    limit: int = 10,
    similarity_threshold: float = 0.6,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Discover potential relationships for an entity.

    Compares vector similarity with graph distance to find entities that
    are semantically related but poorly connected in the graph.  Returns
    suggestions only — does NOT create any edges.

    Args:
        entity_id: The entity to scan for bridge opportunities.
        limit: Maximum suggestions to return (default 10).
        similarity_threshold: Minimum cosine similarity (default 0.6).
        project_id: Optional project scope filter.
    """
    _t0 = time.monotonic()
    return await timed_call("semantic_radar", _service.semantic_radar(  # type: ignore[union-attr]
        entity_id=entity_id,
        limit=limit,
        similarity_threshold=similarity_threshold,
        project_id=project_id,
    ), MCP_OP_TIMEOUT_SEARCH, dispatch_t0=_t0)


async def find_semantic_opportunities(
    project_id: str | None = None,
    similarity_threshold: float = 0.6,
    limit: int = 20,
    min_graph_distance: int = 3,
) -> dict[str, Any]:
    """Scan graph for entity pairs that should be connected.

    Batch analysis across all entities (or a single project).  Surfaces
    pairs that are semantically close but structurally distant.

    Args:
        project_id: Optional project scope filter.
        similarity_threshold: Minimum cosine similarity (default 0.6).
        limit: Maximum opportunities to return (default 20).
        min_graph_distance: Minimum graph hops to qualify (default 3).
    """
    _t0 = time.monotonic()
    return await timed_call("find_semantic_opportunities", _service.find_semantic_opportunities(  # type: ignore[union-attr]
        project_id=project_id,
        similarity_threshold=similarity_threshold,
        limit=limit,
        min_graph_distance=min_graph_distance,
    ), MCP_OP_TIMEOUT_SEARCH, dispatch_t0=_t0)
