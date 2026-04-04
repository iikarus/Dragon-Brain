"""Search operations for the Exocortex memory system.

Provides vector search, spreading-activation, path traversal, hologram, and
point-in-time queries.
"""

import logging
from typing import TYPE_CHECKING, Any

from claude_memory.search_advanced import SearchAdvancedMixin

if TYPE_CHECKING:  # pragma: no cover
    from .activation import ActivationEngine
    from .context_manager import ContextManager
    from .interfaces import Embedder, VectorStore
    from .repository import MemoryRepository
    from .router import QueryRouter
    from .schema import SearchResult

logger = logging.getLogger(__name__)


class SearchMixin(SearchAdvancedMixin):
    """Search / traversal methods — mixed into MemoryService.

    Inherits ``search_associative`` and ``get_hologram`` from SearchAdvancedMixin.
    """

    repo: "MemoryRepository"
    embedder: "Embedder"
    vector_store: "VectorStore"
    router: "QueryRouter"
    activation_engine: "ActivationEngine"
    context_manager: "ContextManager"

    async def get_neighbors(
        self, entity_id: str, depth: int = 1, limit: int = 20, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Retrieve entities connected within a given hop depth."""
        depth = max(depth, 1)
        query = f"""
        MATCH (n)-[*1..{depth}]-(m)
        WHERE n.id = $entity_id
        RETURN distinct m
        SKIP $offset
        LIMIT $limit
        """
        res = self.repo.execute_cypher(
            query, {"entity_id": entity_id, "limit": limit, "offset": offset}
        )
        nodes = [row[0].properties for row in res.result_set if row]
        for n in nodes:
            n.pop("embedding", None)
        return nodes

    async def traverse_path(self, from_id: str, to_id: str) -> list[dict[str, Any]]:
        """Find the shortest path between two entities.

        FalkorDB requires directed shortestPath traversals, so we try
        both directions (forward and reverse) and return whichever succeeds.
        """

        def _extract_path(res: Any) -> list[dict[str, Any]]:
            """Extract node properties from a FalkorDB shortestPath result."""
            path_data: list[dict[str, Any]] = []
            if res.result_set and res.result_set[0]:
                path_obj = res.result_set[0][0]
                if hasattr(path_obj, "nodes"):
                    nodes = path_obj.nodes() if callable(path_obj.nodes) else path_obj.nodes
                    for node in nodes:
                        props = node.properties
                        props.pop("embedding", None)
                        path_data.append(props)
            return path_data

        params = {"start": from_id, "end": to_id}

        # Try forward direction first
        fwd_query = """
        MATCH (a:Entity {id: $start}), (b:Entity {id: $end})
        WITH shortestPath((a)-[*..10]->(b)) AS p
        RETURN p
        """
        try:
            res = self.repo.execute_cypher(fwd_query, params)
            path_data = _extract_path(res)
            if path_data:
                return path_data
        except Exception:  # noqa: S110  # nosec B110
            # Forward traversal failed (e.g. no directed path), try reverse
            pass

        # Try reverse direction
        rev_query = """
        MATCH (a:Entity {id: $start}), (b:Entity {id: $end})
        WITH shortestPath((b)-[*..10]->(a)) AS p
        RETURN p
        """
        res = self.repo.execute_cypher(rev_query, params)
        path_data = _extract_path(res)
        if path_data:
            path_data.reverse()
        return path_data

    async def find_cross_domain_patterns(
        self, entity_id: str, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Find nodes in different projects connected to this entity."""
        query = """
        MATCH (n:Entity {id: $entity_id})
        MATCH (n)-[*1..3]-(m:Entity)
        WHERE m.project_id <> n.project_id
        RETURN distinct m
        LIMIT $limit
        """
        res = self.repo.execute_cypher(query, {"entity_id": entity_id, "limit": limit})
        nodes = [row[0].properties for row in res.result_set if row]
        for n in nodes:
            n.pop("embedding", None)
        return nodes

    async def get_evolution(self, entity_id: str) -> list[dict[str, Any]]:
        """Retrieve the evolution (history/observations) of an entity."""
        query = """
        MATCH (e:Entity {id: $entity_id})-[:HAS_OBSERVATION]->(o)
        RETURN o
        ORDER BY o.created_at DESC
        """
        res = self.repo.execute_cypher(query, {"entity_id": entity_id})
        nodes = [row[0].properties for row in res.result_set if row]
        for n in nodes:
            n.pop("embedding", None)
        return nodes

    async def point_in_time_query(self, query_text: str, as_of: str) -> list[dict[str, Any]]:
        """Execute a search considering only knowledge known before `as_of`."""
        vec = self.embedder.encode(query_text)

        # Use VectorStore with time filter
        vector_results = await self.vector_store.search(
            vector=vec, limit=10, filter={"created_at_lt": as_of}
        )

        if not vector_results:
            return []

        # Hydrate from Graph
        ids = [item["_id"] for item in vector_results]
        graph_data = self.repo.get_subgraph(ids, depth=0)

        # Flatten
        nodes = list(graph_data["nodes"])
        for n in nodes:
            n.pop("embedding", None)
        return nodes

    async def search(  # noqa: PLR0913, C901
        self,
        query: str,
        limit: int = 5,
        project_id: str | None = None,
        offset: int = 0,
        mmr: bool = False,
        strategy: str | None = None,
        deep: bool = False,
    ) -> list["SearchResult"]:
        """Search for entities, optionally routing via QueryRouter.

        When ``strategy`` is provided, dispatches via :class:`QueryRouter`:
        - ``'auto'`` — auto-classify intent from the query text
        - ``'semantic'`` / ``'associative'`` / ``'temporal'`` / ``'relational'``
        When ``strategy`` is None, uses direct vector search (default).
        """
        from .router import QueryIntent  # noqa: PLC0415
        from .schema import SearchResult  # noqa: PLC0415

        if not query:
            return []

        # Route through QueryRouter if strategy is specified
        try:
            if strategy is not None:
                intent = None if strategy == "auto" else QueryIntent(strategy)
                results = await self.router.route(
                    query,
                    self,  # type: ignore[arg-type]  # self is MemoryService at runtime
                    intent=intent,
                    limit=limit,
                    project_id=project_id,
                )
                # router.route may return dicts (temporal/relational) or SearchResults
                if results and isinstance(results[0], dict):
                    return [
                        SearchResult(
                            id=r.get("id", ""),
                            name=r.get("name", "Unknown"),
                            node_type=r.get("node_type", "Entity"),
                            project_id=r.get("project_id", "unknown"),
                            content=r.get("description", ""),
                            score=0.0,
                            distance=0.0,
                        )
                        for r in results
                    ]
                return results

            # 1. Embed Query
            vec = self.embedder.encode(query)

            # 2. Search Qdrant (Vector Engine)
            search_filter: dict[str, Any] | None = None
            if project_id:
                search_filter = {"project_id": project_id}

            if mmr:
                vector_results = await self.vector_store.search_mmr(
                    vector=vec, limit=limit, filter=search_filter
                )
            else:
                vector_results = await self.vector_store.search(
                    vector=vec, limit=limit, filter=search_filter, offset=offset
                )

            if not vector_results:
                return []

            # 3. Hydrate from Graph
            # We have IDs, fetch full nodes.
            ids = [item["_id"] for item in vector_results]

            # We can use get_subgraph with depth 0 (or 1 for deep) to get nodes
            graph_depth = 1 if deep else 0
            graph_data = self.repo.get_subgraph(ids, depth=graph_depth)
            nodes_map = {n["id"]: n for n in graph_data["nodes"]}

            # 4. Fire-and-forget salience update (non-blocking)
            self._fire_salience_update(ids)  # type: ignore[attr-defined]
            salience_map = {
                nid: props.get("salience_score", 0.0) for nid, props in nodes_map.items()
            }

            results = []
            for v_res in vector_results:
                node_id = v_res["_id"]
                if node_id in nodes_map:
                    node_props = nodes_map[node_id]

                    # Observations are always hydrated — they are the entity's
                    # own content and should never return as [].
                    # Relationships are only included when deep=True.
                    obs_query = (
                        "MATCH (e {id: $eid})-[:HAS_OBSERVATION]->(o) "
                        "RETURN o.content ORDER BY o.created_at ASC"
                    )
                    obs_res = self.repo.execute_cypher(obs_query, {"eid": node_id})
                    observations: list[str] = [row[0] for row in obs_res.result_set if row[0]]
                    relationships: list[dict[str, str]] = []
                    if deep:
                        relationships = [
                            e
                            for e in graph_data["edges"]
                            if e.get("src") == node_id or e.get("dst") == node_id
                        ]

                    results.append(
                        SearchResult(
                            id=node_id,
                            name=node_props.get("name", "Unknown"),
                            node_type=node_props.get("node_type", "Entity"),
                            project_id=node_props.get("project_id", "unknown"),
                            content=node_props.get("description", ""),
                            score=v_res["_score"],
                            distance=1.0 - v_res["_score"],
                            salience_score=salience_map.get(
                                node_id,
                                node_props.get("salience_score", 0.0),
                            ),
                            observations=observations,
                            relationships=relationships,
                        )
                    )
            return results
        except (ConnectionError, TimeoutError, OSError, ValueError):
            logger.error("search failed for query=%r", query, exc_info=True)
            return []
