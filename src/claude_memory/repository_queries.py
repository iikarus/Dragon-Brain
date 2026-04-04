"""FalkorDB query methods — temporal, timeline, health, and bottles queries.

Extracted from repository.py as a mixin to keep each file under 300 lines.
Mixed into MemoryRepository at runtime.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from claude_memory.retry import retry_on_transient

logger = logging.getLogger(__name__)


class RepositoryQueryMixin:
    """Query/read methods mixed into MemoryRepository.

    Expects ``self.select_graph()`` to be defined by the host class.
    """

    # -- Timeline / temporal queries ----------------------------------------

    @retry_on_transient()
    def query_timeline(
        self,
        start: str,
        end: str,
        limit: int = 20,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch entities within a time window, ordered by occurred_at.

        Falls back to created_at for entities without occurred_at.
        """
        graph = self.select_graph()  # type: ignore[attr-defined]
        if project_id:
            query = """
            MATCH (n)
            WHERE COALESCE(n.occurred_at, n.created_at) >= $start
              AND COALESCE(n.occurred_at, n.created_at) <= $end
              AND n.project_id = $project_id
              AND n.node_type IS NOT NULL
            RETURN n
            ORDER BY COALESCE(n.occurred_at, n.created_at) DESC
            LIMIT $limit
            """
            params = {
                "start": start,
                "end": end,
                "project_id": project_id,
                "limit": limit,
            }
        else:
            query = """
            MATCH (n)
            WHERE COALESCE(n.occurred_at, n.created_at) >= $start
              AND COALESCE(n.occurred_at, n.created_at) <= $end
              AND n.node_type IS NOT NULL
            RETURN n
            ORDER BY COALESCE(n.occurred_at, n.created_at) DESC
            LIMIT $limit
            """
            params = {"start": start, "end": end, "limit": limit}
        result = graph.query(query, params)
        return [row[0].properties for row in result.result_set if row]

    @retry_on_transient()
    def get_temporal_neighbors(
        self,
        entity_id: str,
        direction: str = "both",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Find entities connected by temporal edges.

        Args:
            entity_id: The anchor entity ID.
            direction: 'before', 'after', or 'both'.
            limit: Max results.
        """
        graph = self.select_graph()  # type: ignore[attr-defined]
        temporal_types = "PRECEDED_BY|EVOLVED_FROM|SUPERSEDES|CONCURRENT_WITH"
        if direction == "before":
            query = f"""
            MATCH (n:Entity {{id: $entity_id}})<-[r:{temporal_types}]-(m:Entity)
            RETURN m
            ORDER BY COALESCE(m.occurred_at, m.created_at) DESC
            LIMIT $limit
            """
        elif direction == "after":
            query = f"""
            MATCH (n:Entity {{id: $entity_id}})-[r:{temporal_types}]->(m:Entity)
            RETURN m
            ORDER BY COALESCE(m.occurred_at, m.created_at) ASC
            LIMIT $limit
            """
        else:
            query = f"""
            MATCH (n:Entity {{id: $entity_id}})-[r:{temporal_types}]-(m:Entity)
            RETURN DISTINCT m
            ORDER BY COALESCE(m.occurred_at, m.created_at) ASC
            LIMIT $limit
            """
        result = graph.query(query, {"entity_id": entity_id, "limit": limit})
        return [row[0].properties for row in result.result_set if row]

    @retry_on_transient()
    def create_temporal_edge(
        self,
        from_id: str,
        to_id: str,
        edge_type: str = "PRECEDED_BY",
        properties: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a temporal relationship between two entities.

        DEAD CODE — no production callers (audit 2026-02-12).
        Kept for API completeness and future use.

        Args:
            from_id: Source entity ID.
            to_id: Target entity ID.
            edge_type: One of the temporal EdgeType values.
            properties: Optional edge properties.
        """
        graph = self.select_graph()  # type: ignore[attr-defined]
        props = properties.copy() if properties else {}
        if "created_at" not in props:
            props["created_at"] = datetime.now(UTC).isoformat()

        query = f"""
        MATCH (a:Entity {{id: $from_id}}), (b:Entity {{id: $to_id}})
        CREATE (a)-[r:{edge_type}]->(b)
        SET r = $props
        RETURN type(r) AS rel_type, a.id AS from_id, b.id AS to_id
        """
        result = graph.query(
            query,
            {"from_id": from_id, "to_id": to_id, "props": props},
        )
        if not result.result_set:
            return {"error": "One or both entities not found"}
        row = result.result_set[0]
        return {
            "rel_type": row[0],
            "from_id": row[1],
            "to_id": row[2],
        }

    # -- Bottles (message-in-a-bottle entities) -----------------------------

    @retry_on_transient()
    def get_bottles(
        self,
        limit: int = 10,
        search_text: str | None = None,
        before_date: str | None = None,
        after_date: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Query 'Bottle' entities with optional text/date/project filters."""
        graph = self.select_graph()  # type: ignore[attr-defined]
        conditions: list[str] = ["n.name CONTAINS 'Bottle'"]
        params: dict[str, Any] = {"limit": limit}

        if search_text:
            conditions.append("(n.name CONTAINS $text OR n.description CONTAINS $text)")
            params["text"] = search_text
        if before_date:
            conditions.append("COALESCE(n.occurred_at, n.created_at) <= $before")
            params["before"] = before_date
        if after_date:
            conditions.append("COALESCE(n.occurred_at, n.created_at) >= $after")
            params["after"] = after_date
        if project_id:
            conditions.append("n.project_id = $pid")
            params["pid"] = project_id

        where_clause = f"WHERE {' AND '.join(conditions)}"
        query = f"""
        MATCH (n:Entity)
        {where_clause}
        RETURN n
        ORDER BY COALESCE(n.occurred_at, n.created_at) DESC
        LIMIT $limit
        """
        result = graph.query(query, params)
        return [row[0].properties for row in result.result_set if row]

    # -- Graph health & edges -----------------------------------------------

    @retry_on_transient()
    def get_graph_health(self) -> dict[str, Any]:
        """Compute basic graph health metrics.

        Returns a dict with node count, edge count, density, orphan count, and avg degree.
        Community count is excluded — computed at the service layer via ClusteringService.
        """
        graph = self.select_graph()  # type: ignore[attr-defined]

        # Node count
        node_result = graph.query("MATCH (n:Entity) RETURN count(n)")
        total_nodes: int = int(node_result.result_set[0][0]) if node_result.result_set else 0

        # Edge count
        edge_result = graph.query("MATCH (:Entity)-[r]->(:Entity) RETURN count(r)")
        total_edges: int = int(edge_result.result_set[0][0]) if edge_result.result_set else 0

        # Orphan count (nodes with zero relationships)
        orphan_result = graph.query("MATCH (n:Entity) WHERE NOT (n)--() RETURN count(n)")
        orphan_count: int = int(orphan_result.result_set[0][0]) if orphan_result.result_set else 0

        # Density: edges / max_possible_edges  (directed graph)
        max_edges = total_nodes * (total_nodes - 1) if total_nodes > 1 else 1
        density = total_edges / max_edges

        # Average degree: total_edges / total_nodes (each edge counted once)
        avg_degree = total_edges / total_nodes if total_nodes > 0 else 0.0

        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "density": round(density, 6),
            "orphan_count": orphan_count,
            "avg_degree": round(avg_degree, 2),
        }

    @retry_on_transient()
    def get_all_edges(self) -> list[dict[str, Any]]:
        """Fetch all edges between Entity nodes for gap detection."""
        graph = self.select_graph()  # type: ignore[attr-defined]
        result = graph.query("MATCH (a:Entity)-[r]->(b:Entity) RETURN a.id, b.id, type(r)")
        return [
            {"source": row[0], "target": row[1], "type": row[2]} for row in result.result_set if row
        ]

    @retry_on_transient()
    def get_all_node_ids(self, limit: int = 10000) -> list[str]:
        """Return all Entity node IDs for diagnostics."""
        graph = self.select_graph()  # type: ignore[attr-defined]
        result = graph.query("MATCH (n:Entity) RETURN n.id LIMIT $limit", {"limit": limit})
        return [row[0] for row in result.result_set if row]
