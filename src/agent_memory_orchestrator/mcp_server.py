from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .config import Settings
from .memory_service import MemoryService
from .orchestrator import OrchestratorService


def create_server(settings: Settings) -> FastMCP:
    mcp = FastMCP("agent-memory-orchestrator")
    memory = MemoryService(settings)
    memory.init_db()
    orchestrator = OrchestratorService(settings)

    @mcp.tool()
    def health_ping() -> dict:
        return {"ok": True, "service": "agent-memory-orchestrator"}

    @mcp.tool()
    def config_view() -> dict:
        return {
            "local_only": settings.local_only,
            "mcp_transport": settings.mcp_transport,
            "mcp_host": settings.mcp_host,
            "mcp_port": settings.mcp_port,
            "db_path": str(settings.db_path),
            "export_dir": str(settings.export_dir),
            "embedding_dims": settings.embedding_dims,
            "embedding_model": settings.embedding_model,
            "reranker_model": settings.reranker_model,
            "vector_backend": settings.vector_backend,
            "approval_mode": settings.approval_mode,
            "owner_user_id": settings.owner_user_id,
            "workspace_id": settings.workspace_id,
            "project_id": settings.project_id,
            "visibility_scope": settings.visibility_scope,
            "sensitivity_level": settings.sensitivity_level,
            "consensus_threshold": settings.consensus_threshold,
            "max_review_rounds": settings.max_review_rounds,
        }

    @mcp.tool()
    def memory_write(
        session_id: str,
        agent: str,
        event_type: str,
        content: str,
        metadata_json: str = "{}",
        create_memory: bool = True,
    ) -> dict:
        if not memory.session_exists(session_id):
            memory.create_session(session_id=session_id, title=session_id)
        metadata = json.loads(metadata_json)
        event = memory.add_event(
            session_id=session_id,
            agent=agent,
            event_type=event_type,
            content=content,
            metadata=metadata,
            source_app=agent,
            process=create_memory,
        )
        memory_id = None
        if create_memory:
            row = memory.conn.execute(
                "SELECT id FROM memory_units WHERE source_event_id = ? ORDER BY created_at DESC LIMIT 1",
                (event.id,),
            ).fetchone()
            memory_id = row["id"] if row else None
        return {"event_id": event.id, "memory_id": memory_id}

    @mcp.tool()
    def memory_search(query: str, session_id: str = "", limit: int = 10, include_historical: bool = False) -> dict:
        target_session = session_id or None
        results = memory.search_memories(
            query=query,
            session_id=target_session,
            limit=limit,
            include_historical=include_historical,
        )
        return {"count": len(results), "results": results}

    @mcp.tool()
    def memory_metrics() -> dict:
        return memory.inspect_metrics()

    @mcp.tool()
    def memory_rebuild_indexes() -> dict:
        return memory.rebuild_indexes()

    @mcp.tool()
    def memory_timeline(session_id: str, limit: int = 50) -> dict:
        events = memory.timeline(session_id=session_id, limit=limit)
        return {"count": len(events), "events": events}

    @mcp.tool()
    def memory_export(out_path: str = "", session_id: str = "") -> dict:
        target = Path(out_path) if out_path else settings.export_dir / "memory_snapshot.jsonl"
        rows = memory.export_snapshot(out_path=target, session_id=(session_id or None))
        return {"rows": rows, "out_path": str(target.resolve())}

    @mcp.tool()
    def memory_import(in_path: str) -> dict:
        rows = memory.import_snapshot(Path(in_path))
        return {"rows": rows}

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


def main() -> None:
    settings = Settings.load()
    mcp = create_server(settings)
    if settings.mcp_transport == "stdio":
        mcp.run(transport="stdio")
        return
    mcp.run(transport="sse", host=settings.mcp_host, port=settings.mcp_port)


if __name__ == "__main__":
    main()
