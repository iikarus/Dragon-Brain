"""Streamlit dashboard for exploring and managing the Exocortex memory graph."""

import asyncio
import datetime
import subprocess
from typing import Any

import nest_asyncio
import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network

from claude_memory.embedding import EmbeddingService
from claude_memory.tools import MemoryService

# Allow nested event loops so asyncio.run() works inside Streamlit callbacks.
nest_asyncio.apply()

st.set_page_config(layout="wide", page_title="Memory Explorer")


@st.cache_resource
def get_service() -> MemoryService:
    """Create and cache a MemoryService instance."""
    embedder = EmbeddingService()
    return MemoryService(embedding_service=embedder)


def get_graph_data(limit: int = 100, focus: str = "") -> Any:
    """Query the graph for node/relationship data, optionally focused on a node."""
    service = get_service()

    if focus:
        q = """
        MATCH (n:Entity)
        WHERE n.id = $focus OR n.name CONTAINS $focus
        OPTIONAL MATCH (n)-[r]-(m:Entity)
        RETURN n, r, m LIMIT $limit
        """
        return service.repo.execute_cypher(q, {"focus": focus, "limit": limit})
    else:
        q = """
        MATCH (n:Entity)
        OPTIONAL MATCH (n)-[r]->(m:Entity)
        RETURN n, r, m LIMIT $limit
        """
        return service.repo.execute_cypher(q, {"limit": limit})


def get_stats() -> tuple[int, int]:
    """Return total node and edge counts from the graph."""
    service = get_service()
    nodes = service.repo.execute_cypher("MATCH (n) RETURN count(n)").result_set[0][0]
    edges = service.repo.execute_cypher("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    return nodes, edges


# ─── Tab renderers (extracted from main to reduce C901 complexity) ───


def _render_explorer_tab() -> None:
    """Render the Explorer tab with graph visualization."""
    st.header("Graph View")
    col1, col2 = st.columns([1, 2])
    with col1:
        limit = st.slider("Max Nodes", 10, 500, 100)
    with col2:
        focus = st.text_input("Focus Node (ID or Name)", help="Leave empty for global view")

    if st.button("Refresh Graph"):
        res = get_graph_data(limit, focus)

        net = Network(height="600px", width="100%", bgcolor="#222222", font_color="white")

        for row in res.result_set:
            n = row[0]
            r = row[1]
            m = row[2]

            n_id = n.properties.get("id", n.properties.get("name", "unknown"))
            net.add_node(
                n_id,
                label=n.properties.get("name", "Unknown"),
                title=str(n.properties),
            )

            if r is not None and m is not None:
                m_id = m.properties.get("id", m.properties.get("name", "unknown"))
                net.add_node(
                    m_id,
                    label=m.properties.get("name", "Unknown"),
                    title=str(m.properties),
                )
                net.add_edge(n_id, m_id, title=r.relation)

        net.repulsion()
        net.save_graph("graph.html")

        with open("graph.html", encoding="utf-8") as f:
            source_code = f.read()
        components.html(source_code, height=600)


def _render_search_tab(service: MemoryService) -> None:
    """Render the Search tab with semantic search."""
    st.header("Semantic Search")
    query = st.text_input("Query")
    if query:
        results = asyncio.run(service.search(query))  # nest_asyncio makes this safe
        for res in results:
            with st.expander(f"{res.name} (Score: {res.score:.2f})"):
                st.json(res)


def _render_maintenance_tab(service: MemoryService) -> None:
    """Render the Maintenance tab with stale entity scanning."""
    st.header("Maintenance Tools")

    st.subheader("Stale Entities")
    days = st.number_input("Days Inactive", value=30)
    if st.button("Scan"):
        stale = asyncio.run(service.get_stale_entities(days))  # nest_asyncio makes this safe
        st.write(f"Found {len(stale)} stale entities.")
        st.dataframe(stale)


def _render_shutdown_sidebar() -> None:
    """Render the sidebar shutdown controls."""
    st.sidebar.markdown("---")
    st.sidebar.subheader("System Control")
    if st.sidebar.button("⛔ Safe Shutdown"):
        with st.sidebar.status("Initiating Shutdown Sequence...") as status:
            # 1. Perform Backup
            status.write("💾 Performing Backup...")

            tag = f"shutdown_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"

            try:
                res = subprocess.run(  # noqa: S603
                    ["python", "scripts/backup_restore.py", "save", "--tag", tag],  # noqa: S607
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if res.returncode == 0:
                    status.write(f"✅ Backup Successful: {tag}")
                else:
                    status.write("❌ Backup Failed!")
                    status.write(res.stderr)
                    st.error("Shutdown functions halted. Backup failed.")
                    st.code(res.stderr)
                    return
            except (OSError, ValueError) as e:
                st.error(f"Backup Error: {e}")
                return

            # 2. Stop Containers
            status.write("🛑 Stopping Exocortex...")

            try:
                cmd = [
                    "docker",
                    "ps",
                    "-q",
                    "--filter",
                    "label=com.docker.compose.project=claude-memory-mcp",
                ]
                ids_res = subprocess.run(  # noqa: S603
                    cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                ids = ids_res.stdout.strip().split()

                if not ids:
                    status.write("System already stopped? (No containers found)")
                else:
                    subprocess.run(  # noqa: S603
                        ["docker", "stop", *ids],  # noqa: S607
                        check=False,
                    )
                    status.write("✅ System Shutdown Complete.")
                    st.stop()  # Stop the streamlit script

            except (OSError, ValueError) as e:
                status.write(f"❌ Shutdown Failed: {e}")


def main() -> None:
    """Render the Streamlit dashboard with Explorer, Search, and Maintenance modes."""
    st.title("🧠 Memory System Visual Explorer")

    service = get_service()

    # Sidebar Stats
    nodes, edges = get_stats()
    st.sidebar.metric("Total Nodes", nodes)
    st.sidebar.metric("Relationships", edges)

    menu = st.sidebar.radio("Mode", ["Explorer", "Search", "Maintenance"])

    if menu == "Explorer":
        _render_explorer_tab()
    elif menu == "Search":
        _render_search_tab(service)
    elif menu == "Maintenance":
        _render_maintenance_tab(service)

    _render_shutdown_sidebar()


if __name__ == "__main__":
    main()
