"""MCP server exposing Claude Memory tools via stdio transport."""

import logging
import time
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP

from claude_memory.clustering import ClusteringService
from claude_memory.embedding import EmbeddingService
from claude_memory.librarian import LibrarianAgent
from claude_memory.schema import (
    BreakthroughParams,
    CertaintyLevel,
    EdgeType,
    EntityCommitReceipt,
    EntityCreateParams,
    EntityDeleteParams,
    EntityUpdateParams,
    ObservationParams,
    RelationshipCreateParams,
    RelationshipDeleteParams,
    SessionEndParams,
    SessionStartParams,
)
from claude_memory.timeout import MCP_OP_TIMEOUT, MCP_OP_TIMEOUT_SEARCH, timed_call
from claude_memory.tools import MemoryService
from claude_memory.tools_extra import (
    configure as _configure_extra_tools,
)
from claude_memory.tools_extra import (
    create_memory_type,  # noqa: F401 — re-export for backward compat
    find_knowledge_gaps,  # noqa: F401
    get_bottles,  # noqa: F401
    get_temporal_neighbors,  # noqa: F401
    graph_health,  # noqa: F401
    query_timeline,  # noqa: F401
    run_librarian_cycle,  # noqa: F401
    search_associative,  # noqa: F401
)

# Initialize MCP Server
mcp = FastMCP("claude-memory")

# Initialize Service
# Wire up dependencies explicitly
embedder = EmbeddingService()
service = MemoryService(embedding_service=embedder)
clustering = ClusteringService()
librarian = LibrarianAgent(service, clustering)

# Register extra tool handlers (temporal, search variants, health, librarian)
_configure_extra_tools(mcp, service, librarian)


@mcp.tool()
async def create_entity(  # noqa: PLR0913
    name: str,
    node_type: str,
    project_id: str,
    properties: dict[str, Any] | None = None,
    certainty: CertaintyLevel = "confirmed",
    evidence: list[str] | None = None,
) -> EntityCommitReceipt:
    """Create a new entity in the memory graph."""
    if evidence is None:
        evidence = []
    if properties is None:
        properties = {}
    params = EntityCreateParams(
        name=name,
        node_type=node_type,
        project_id=project_id,
        properties=properties,
        certainty=certainty,
        evidence=evidence,
    )
    _t0 = time.monotonic()
    return await timed_call("create_entity", service.create_entity(params), MCP_OP_TIMEOUT, dispatch_t0=_t0)


@mcp.tool()
async def update_entity(
    entity_id: str,
    properties: dict[str, Any],
    reason: str | None = None,
) -> dict[str, Any]:
    """Updates properties of an existing entity."""
    params = EntityUpdateParams(
        entity_id=entity_id,
        properties=properties,
        reason=reason,
    )
    _t0 = time.monotonic()
    return await timed_call("update_entity", service.update_entity(params), MCP_OP_TIMEOUT, dispatch_t0=_t0)


@mcp.tool()
async def delete_entity(
    entity_id: str,
    reason: str,
    soft_delete: bool = True,
) -> dict[str, Any]:
    """Deletes (or soft deletes) an entity."""
    params = EntityDeleteParams(
        entity_id=entity_id,
        reason=reason,
        soft_delete=soft_delete,
    )
    _t0 = time.monotonic()
    return await timed_call("delete_entity", service.delete_entity(params), MCP_OP_TIMEOUT, dispatch_t0=_t0)


@mcp.tool()
async def create_relationship(  # noqa: PLR0913
    from_entity: str,
    to_entity: str,
    relationship_type: EdgeType,
    properties: dict[str, Any] | None = None,
    confidence: float = 1.0,
    weight: float = 1.0,
) -> dict[str, Any]:
    """Create a relationship between two entities. Weight (0-1) indicates strength."""
    if properties is None:
        properties = {}
    params = RelationshipCreateParams(
        from_entity=from_entity,
        to_entity=to_entity,
        relationship_type=relationship_type,
        properties=properties,
        confidence=confidence,
        weight=weight,
    )
    _t0 = time.monotonic()
    return await timed_call("create_relationship", service.create_relationship(params), MCP_OP_TIMEOUT, dispatch_t0=_t0)


@mcp.tool()
async def delete_relationship(
    relationship_id: str,
    reason: str,
) -> dict[str, Any]:
    """Deletes a relationship."""
    params = RelationshipDeleteParams(
        relationship_id=relationship_id,
        reason=reason,
    )
    _t0 = time.monotonic()
    return await timed_call("delete_relationship", service.delete_relationship(params), MCP_OP_TIMEOUT, dispatch_t0=_t0)


@mcp.tool()
async def add_observation(
    entity_id: str,
    content: str,
    certainty: CertaintyLevel = "confirmed",
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    """Adds an observation node linked to an entity."""
    if evidence is None:
        evidence = []
    params = ObservationParams(
        entity_id=entity_id,
        content=content,
        certainty=certainty,
        evidence=evidence,
    )
    _t0 = time.monotonic()
    return await timed_call("add_observation", service.add_observation(params), MCP_OP_TIMEOUT, dispatch_t0=_t0)


@mcp.tool()
async def start_session(project_id: str, focus: str) -> dict[str, Any]:
    """Starts a new session context."""
    params = SessionStartParams(project_id=project_id, focus=focus)
    _t0 = time.monotonic()
    return await timed_call("start_session", service.start_session(params), MCP_OP_TIMEOUT, dispatch_t0=_t0)


@mcp.tool()
async def end_session(
    session_id: str, summary: str, outcomes: list[str] | None = None
) -> dict[str, Any]:
    """Ends a session and records summary."""
    if outcomes is None:
        outcomes = []
    params = SessionEndParams(session_id=session_id, summary=summary, outcomes=outcomes)
    _t0 = time.monotonic()
    return await timed_call("end_session", service.end_session(params), MCP_OP_TIMEOUT, dispatch_t0=_t0)


@mcp.tool()
async def record_breakthrough(
    name: str,
    moment: str,
    session_id: str,
    analogy_used: str | None = None,
    concepts_unlocked: list[str] | None = None,
) -> dict[str, Any]:
    """Record a learning breakthrough."""
    if concepts_unlocked is None:
        concepts_unlocked = []
    params = BreakthroughParams(
        name=name,
        moment=moment,
        session_id=session_id,
        analogy_used=analogy_used,
        concepts_unlocked=concepts_unlocked,
    )
    _t0 = time.monotonic()
    return await timed_call("record_breakthrough", service.record_breakthrough(params), MCP_OP_TIMEOUT, dispatch_t0=_t0)


@mcp.tool()
async def get_neighbors(
    entity_id: str, depth: int = 1, limit: int = 20, offset: int = 0
) -> list[dict[str, Any]]:
    """Retrieve neighboring entities up to a certain depth."""
    _t0 = time.monotonic()
    return await timed_call("get_neighbors", service.get_neighbors(entity_id, depth, limit, offset), MCP_OP_TIMEOUT, dispatch_t0=_t0)


@mcp.tool()
async def traverse_path(from_id: str, to_id: str) -> list[dict[str, Any]]:
    """Find the shortest path between two entities."""
    _t0 = time.monotonic()
    return await timed_call("traverse_path", service.traverse_path(from_id, to_id), MCP_OP_TIMEOUT, dispatch_t0=_t0)


@mcp.tool()
async def find_cross_domain_patterns(entity_id: str, limit: int = 10) -> list[dict[str, Any]]:
    """Analyzes the graph for non-obvious connections between disparate domains."""
    _t0 = time.monotonic()
    return await timed_call("find_cross_domain_patterns", service.find_cross_domain_patterns(entity_id, limit), MCP_OP_TIMEOUT, dispatch_t0=_t0)


@mcp.tool()
async def get_evolution(entity_id: str) -> list[dict[str, Any]]:
    """Retrieve the evolution (history/observations) of an entity."""
    _t0 = time.monotonic()
    return await timed_call("get_evolution", service.get_evolution(entity_id), MCP_OP_TIMEOUT, dispatch_t0=_t0)


@mcp.tool()
async def point_in_time_query(query_text: str, as_of: str) -> list[dict[str, Any]]:
    """Execute a search considering only knowledge known before `as_of`."""
    _t0 = time.monotonic()
    return await timed_call("point_in_time_query", service.point_in_time_query(query_text, as_of), MCP_OP_TIMEOUT_SEARCH, dispatch_t0=_t0)


@mcp.tool()
async def archive_entity(entity_id: str) -> dict[str, Any]:
    """Archive an entity (logical hide."""
    _t0 = time.monotonic()
    return await timed_call("archive_entity", service.archive_entity(entity_id), MCP_OP_TIMEOUT, dispatch_t0=_t0)


@mcp.tool()
async def prune_stale(days: int = 30) -> dict[str, Any]:
    """Hard delete archived entities older than N days."""
    _t0 = time.monotonic()
    return await timed_call("prune_stale", service.prune_stale(days), MCP_OP_TIMEOUT, dispatch_t0=_t0)


@mcp.tool()
async def search_memory(  # noqa: PLR0913
    query: str,
    project_id: str | None = None,
    limit: int = 10,
    offset: int = 0,
    mmr: bool = False,
    strategy: str | None = None,
    temporal_window_days: int = 7,
    include_meta: bool = False,
) -> list[dict[str, Any]] | dict[str, Any] | str:
    """Search for entities. mmr=True for diverse results.

    strategy: 'semantic', 'associative', 'temporal', 'relational', or None (hybrid default).
    temporal_window_days: lookback window for temporal queries (default 7).
    include_meta: when True, wraps results with temporal exhaustion metadata.
    """
    _t0 = time.monotonic()
    results = await timed_call("search_memory", service.search(
        query,
        limit,
        project_id,
        offset,
        mmr=mmr,
        strategy=strategy,
        temporal_window_days=temporal_window_days,
    ), MCP_OP_TIMEOUT_SEARCH, dispatch_t0=_t0)
    if not results:
        return "No results found."

    result_dicts = [res.model_dump() for res in results]

    # Return temporal metadata envelope when opted-in
    if include_meta and hasattr(service, "_last_detected_intent"):
        from claude_memory.router import QueryIntent  # noqa: PLC0415
        from claude_memory.schema import HybridSearchResponse  # noqa: PLC0415

        if service._last_detected_intent == QueryIntent.TEMPORAL:
            response = HybridSearchResponse(
                results=results,
                meta={
                    "temporal_exhausted": getattr(service, "_last_temporal_exhausted", False),
                    "temporal_window_days": getattr(service, "_last_temporal_window_days", 7),
                    "temporal_result_count": getattr(service, "_last_temporal_result_count", 0),
                    "suggestion": (
                        "Widen temporal_window_days for more historical results"
                        if getattr(service, "_last_temporal_exhausted", False)
                        else None
                    ),
                },
            )
            return response.model_dump()

    return result_dicts


@mcp.tool()
async def analyze_graph(
    algorithm: Literal["pagerank", "louvain"] = "pagerank",
) -> list[dict[str, Any]]:
    """Runs graph algorithms (pagerank or louvain) to find key entities or communities."""
    _t0 = time.monotonic()
    return await timed_call("analyze_graph", service.analyze_graph(algorithm=algorithm), MCP_OP_TIMEOUT, dispatch_t0=_t0)


@mcp.tool()
async def get_hologram(
    query: str,
    depth: int = 1,
    max_tokens: int = 8000,
) -> dict[str, Any]:
    """Retrieves a 'Hologram' — a connected subgraph relevant to the query."""
    _t0 = time.monotonic()
    return await timed_call("get_hologram", service.get_hologram(query, depth=depth, max_tokens=max_tokens), MCP_OP_TIMEOUT_SEARCH, dispatch_t0=_t0)


@mcp.tool()
async def search_stats() -> dict[str, Any]:
    """Return rolling-window search behaviour statistics (DRIFT-002).

    Reports distribution of retrieval strategies, score percentiles,
    vector score null rates, and latency — useful for detecting
    behavioural drift over time.
    """
    if service._stats is None:
        return {"status": "stats not enabled", "searches_recorded": 0}
    return service._stats.report()


_background_tasks: set[object] = set()  # prevent GC of fire-and-forget tasks

# Buffer depth for outgoing MCP responses.  The MCP SDK uses zero-buffer
# streams (anyio.create_memory_object_stream(0)) which means every response
# write blocks until the stdout pipe drains.  Under burst load this causes
# back-pressure that starves the event loop, preventing new request handlers
# from running.  This buffer decouples handler completion from pipe I/O.
_STDIO_WRITE_BUFFER = 16


async def _run_stdio_buffered() -> None:
    """Launch MCP server with a buffered write stream.

    Prevents stdout back-pressure from stalling the event loop when
    multiple tool responses complete simultaneously.  See GOTCHAS #9.
    """
    import asyncio  # noqa: PLC0415

    import anyio  # noqa: PLC0415
    from mcp.server.stdio import stdio_server  # noqa: PLC0415

    from claude_memory.update_check import check_for_updates  # noqa: PLC0415

    # Fire-and-forget update check inside the running event loop
    task = asyncio.get_event_loop().create_task(check_for_updates())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    async with stdio_server() as (read_stream, write_stream):
        buffered_send, buffered_recv = anyio.create_memory_object_stream(
            _STDIO_WRITE_BUFFER
        )

        async def _drain_to_stdout() -> None:
            async with buffered_recv:
                async for msg in buffered_recv:
                    await write_stream.send(msg)

        async with anyio.create_task_group() as tg:
            tg.start_soon(_drain_to_stdout)
            await mcp._mcp_server.run(
                read_stream,
                buffered_send,
                mcp._mcp_server.create_initialization_options(),
            )


def main() -> None:
    """Launch the MCP server via stdio transport."""
    import anyio  # noqa: PLC0415

    from claude_memory.logging_config import configure_logging  # noqa: PLC0415

    configure_logging()
    logger = logging.getLogger(__name__)

    logger.info("Starting MCP server (stdio)")
    anyio.run(_run_stdio_buffered)


if __name__ == "__main__":
    main()
