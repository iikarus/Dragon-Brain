"""Temporal operations for the Exocortex memory system.

Provides session management, breakthroughs, timeline queries, and time-travel.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:  # pragma: no cover
    from .repository import MemoryRepository
    from .schema import (
        BottleQueryParams,
        BreakthroughParams,
        SessionEndParams,
        SessionStartParams,
        TemporalQueryParams,
    )

logger = logging.getLogger(__name__)


class TemporalMixin:
    """Session/Breakthrough/Timeline methods — mixed into MemoryService."""

    repo: "MemoryRepository"

    async def start_session(self, params: "SessionStartParams") -> dict[str, Any]:
        """Create a new session node in the graph.

        Sessions act as temporal anchors: each session gets an occurred_at
        timestamp and is linked to the previous session via PRECEDED_BY.
        """
        session_id = str(uuid.uuid4())
        timestamp = datetime.now(UTC).isoformat()

        # Generate a session name from focus text for discoverability
        focus_name = params.focus[:80].rstrip(".… ") if params.focus else ""
        session_name = f"Session — {focus_name}" if focus_name else f"Session — {timestamp[:16]}"

        props = {
            "id": session_id,
            "name": session_name,
            "project_id": params.project_id,
            "focus": params.focus,
            "status": "active",
            "created_at": timestamp,
            "occurred_at": timestamp,
            "node_type": "Session",
        }

        query = """
        OPTIONAL MATCH (prev:Session {project_id: $project_id, status: 'closed'})
        WITH prev ORDER BY prev.occurred_at DESC LIMIT 1
        CREATE (s:Session)
        SET s = $props
        WITH s, prev
        FOREACH (_ IN CASE WHEN prev IS NOT NULL THEN [1] ELSE [] END |
            CREATE (prev)-[:PRECEDED_BY {created_at: $timestamp}]->(s)
        )
        RETURN s
        """
        res = self.repo.execute_cypher(
            query,
            {
                "props": props,
                "project_id": params.project_id,
                "timestamp": timestamp,
            },
        )
        return cast(dict[str, Any], res.result_set[0][0].properties)

    async def end_session(self, params: "SessionEndParams") -> dict[str, Any]:
        """Close a session and record its summary and outcomes."""
        timestamp = datetime.now(UTC).isoformat()
        query = """
        MATCH (s:Session)
        WHERE s.id = $session_id
        SET s.status = 'closed'
        SET s.ended_at = $timestamp
        SET s.summary = $summary
        SET s.outcomes = $outcomes
        RETURN s
        """
        res = self.repo.execute_cypher(
            query,
            {
                "session_id": params.session_id,
                "timestamp": timestamp,
                "summary": params.summary,
                "outcomes": params.outcomes,
            },
        )
        if not res.result_set:
            return {"error": "Session not found"}
        return cast(dict[str, Any], res.result_set[0][0].properties)

    async def record_breakthrough(self, params: "BreakthroughParams") -> dict[str, Any]:
        """Create a Breakthrough node linked to its originating session."""
        b_id = str(uuid.uuid4())
        props = {
            "id": b_id,
            "name": params.name,
            "moment": params.moment,
            "analogy": params.analogy_used or "",
            "project_id": "meta",
            "certainty": "confirmed",
            "created_at": datetime.now(UTC).isoformat(),
            "node_type": "Breakthrough",
        }
        res = self.repo.create_node("Breakthrough", props)
        if params.session_id:
            self.repo.create_edge(params.session_id, b_id, "BREAKTHROUGH_IN", {"confidence": 1.0})

        return res  # type: ignore[no-any-return]

    async def query_timeline(
        self,
        params: "TemporalQueryParams",
    ) -> list[dict[str, Any]]:
        """Fetch entities within a time window, ordered by occurred_at."""
        return self.repo.query_timeline(  # type: ignore[no-any-return]
            start=params.start.isoformat(),
            end=params.end.isoformat(),
            limit=params.limit,
            project_id=params.project_id,
        )

    async def get_temporal_neighbors(
        self,
        entity_id: str,
        direction: str = "both",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Find entities connected by temporal edges."""
        return self.repo.get_temporal_neighbors(  # type: ignore[no-any-return]
            entity_id=entity_id,
            direction=direction,
            limit=limit,
        )

    async def get_bottles(
        self,
        params: "BottleQueryParams",
    ) -> list[dict[str, Any]]:
        """Query 'Bottle' entities (messages to future self)."""
        bottles = self.repo.get_bottles(
            limit=params.limit,
            search_text=params.search_text,
            before_date=params.before_date.isoformat() if params.before_date else None,
            after_date=params.after_date.isoformat() if params.after_date else None,
            project_id=params.project_id,
        )

        if params.include_content:
            for bottle in bottles:
                obs_query = """
                MATCH (e:Entity {id: $eid})-[:HAS_OBSERVATION]->(o)
                RETURN o.content
                ORDER BY o.created_at ASC
                """
                result = self.repo.execute_cypher(obs_query, {"eid": bottle["id"]})
                bottle["observations"] = [row[0] for row in result.result_set if row[0]]

        return list(bottles)
