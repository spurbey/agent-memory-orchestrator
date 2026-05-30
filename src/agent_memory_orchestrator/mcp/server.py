from __future__ import annotations

import argparse
import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ..core.config import Settings
from ..orchestration import OrchestratorService
from .tools import MemoryMcpToolService


def create_server(settings: Settings) -> FastMCP:
    mcp = FastMCP("agent-memory-orchestrator")
    memory_tools = MemoryMcpToolService(settings)
    orchestrator = OrchestratorService(settings)

    @mcp.tool()
    def health_ping() -> dict:
        """Check that the local AMO MCP server is alive."""
        return memory_tools.health_ping()

    @mcp.tool()
    def config_view() -> dict:
        """Return local-only AMO runtime configuration."""
        return memory_tools.config_view()

    @mcp.tool()
    def tool_contracts() -> dict:
        """Return stable AMO memory tool contracts for agents/tests."""
        return memory_tools.tool_contracts()

    @mcp.tool()
    def memory_write(
        session_id: str,
        agent: str,
        event_type: str,
        content: str,
        metadata_json: str = "{}",
        create_memory: bool = True,
    ) -> dict:
        """Persist a local event and optionally extract durable memory."""
        return memory_tools.memory_write(
            session_id=session_id,
            agent=agent,
            event_type=event_type,
            content=content,
            metadata_json=metadata_json,
            create_memory=create_memory,
        )

    @mcp.tool()
    def memory_search(query: str, session_id: str = "", limit: int = 10, include_historical: bool = False) -> dict:
        """Search local memory with BM25/vector/KG fusion and provenance."""
        return memory_tools.memory_search(
            query=query,
            session_id=session_id,
            limit=limit,
            include_historical=include_historical,
        )

    @mcp.tool()
    def memory_context_pack(
        query: str,
        session_id: str = "",
        budget: int = 2500,
        limit: int = 12,
        include_historical: bool = False,
    ) -> dict:
        """Build an agent-ready context pack with provenance and exclusions."""
        return memory_tools.memory_context_pack(
            query=query,
            session_id=session_id,
            budget=budget,
            limit=limit,
            include_historical=include_historical,
        )

    @mcp.tool()
    def memory_metrics() -> dict:
        """Inspect memory pipeline/retrieval/consolidation row counts."""
        return memory_tools.memory_metrics()

    @mcp.tool()
    def memory_rebuild_indexes(force_vectors: bool = False) -> dict:
        """Rebuild local FTS/vector indexes from canonical SQLite memory rows."""
        return memory_tools.memory_rebuild_indexes(force_vectors=force_vectors)

    @mcp.tool()
    def memory_timeline(session_id: str, limit: int = 50) -> dict:
        """Read a redacted raw event timeline for one session."""
        return memory_tools.memory_timeline(session_id=session_id, limit=limit)

    @mcp.tool()
    def memory_export(out_path: str = "", session_id: str = "") -> dict:
        """Export canonical local memory rows to JSONL."""
        return memory_tools.memory_export(out_path=out_path, session_id=session_id)

    @mcp.tool()
    def memory_import(in_path: str) -> dict:
        """Import a JSONL memory snapshot into the local SQLite store."""
        return memory_tools.memory_import(in_path=in_path)

    @mcp.tool()
    def amo_graph_search(
        query: str,
        limit: int = 8,
        repo_id: str = "",
        use_vector: bool = True,
        require_vector: bool = False,
        include_raw: bool = False,
        include_historical: bool = False,
    ) -> dict:
        """Explicit Kuzu GraphRAG search. Use only when the user/agent asks AMO memory."""
        return memory_tools.amo_graph_search(
            query=query,
            limit=limit,
            repo_id=repo_id,
            use_vector=use_vector,
            require_vector=require_vector,
            include_raw=include_raw,
            include_historical=include_historical,
        )

    @mcp.tool()
    def amo_current_context(session_id: str = "", limit: int = 8) -> dict:
        """Read current AMO graph context without hook auto-retrieval."""
        return memory_tools.amo_current_context(session_id=session_id, limit=limit)

    @mcp.tool()
    def amo_decision_history(query: str, limit: int = 8) -> dict:
        """Retrieve active and historical decisions from the Kuzu graph."""
        return memory_tools.amo_decision_history(query=query, limit=limit)

    @mcp.tool()
    def amo_work_history(query: str, limit: int = 8) -> dict:
        """Retrieve work-change and commit-linked history from the Kuzu graph."""
        return memory_tools.amo_work_history(query=query, limit=limit)

    @mcp.tool()
    def amo_raw_evidence(query: str, limit: int = 8) -> dict:
        """Retrieve raw evidence refs explicitly; raw evidence is not injected by hooks."""
        return memory_tools.amo_raw_evidence(query=query, limit=limit)

    @mcp.tool()
    def amo_merge_status(session_id: str = "") -> dict:
        """Inspect graph merge status for a session or the central graph."""
        return memory_tools.amo_merge_status(session_id=session_id)

    @mcp.tool()
    def peer_memory_ask(
        query: str,
        session_id: str = "",
        min_confidence: float = 0.72,
        timeout_seconds: float = 45,
    ) -> dict:
        """Ask local memory first; open a trusted peer-agent room only when local confidence is low."""
        return memory_tools.peer_memory_ask(
            query=query,
            session_id=session_id,
            min_confidence=min_confidence,
            timeout_seconds=timeout_seconds,
        )

    @mcp.tool()
    def peer_room_status(room_id: str) -> dict:
        """Inspect peer-agent room status and agent_state."""
        return memory_tools.peer_room_status(room_id=room_id)

    @mcp.tool()
    def peer_room_context(room_id: str) -> dict:
        """Read local peer-agent room context."""
        return memory_tools.peer_room_context(room_id=room_id)

    @mcp.tool()
    def peer_room_messages(room_id: str) -> dict:
        """Read local peer-agent room messages."""
        return memory_tools.peer_room_messages(room_id=room_id)

    @mcp.tool()
    def orchestrator_start(session_id: str, title: str = "") -> dict:
        return orchestrator.start(session_id=session_id, title=(title or None))

    @mcp.tool()
    def orchestrator_submit(
        session_id: str,
        agent: str,
        summary: str,
        confidence: float,
        artifact_uri: str = "",
        blocking_issues: list[str] | None = None,
    ) -> dict:
        return orchestrator.submit(
            session_id=session_id,
            agent=agent,
            summary=summary,
            confidence=confidence,
            artifact_uri=artifact_uri,
            blocking_issues=blocking_issues,
        )

    @mcp.tool()
    def orchestrator_status(session_id: str) -> dict:
        return orchestrator.status(session_id=session_id)

    @mcp.tool()
    def orchestrator_user_decision(session_id: str, decision: str, notes: str = "", decided_by: str = "user") -> dict:
        return orchestrator.user_decision(
            session_id=session_id,
            decision=decision,
            notes=notes,
            decided_by=decided_by,
        )

    return mcp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the local Agent Memory Orchestrator MCP server")
    parser.add_argument("--amo-home", type=Path, help="AMO home directory containing config.json and .data.")
    args = parser.parse_args(argv)
    if args.amo_home:
        os.environ["AMO_HOME"] = str(args.amo_home.expanduser().resolve())

    settings = Settings.load()
    mcp = create_server(settings)
    if settings.mcp_transport == "stdio":
        mcp.run(transport="stdio")
        return
    mcp.run(transport="sse", host=settings.mcp_host, port=settings.mcp_port)


if __name__ == "__main__":
    main()

