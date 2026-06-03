from __future__ import annotations


def test_closed_session_pipeline_workflow_delegates() -> None:
    from agent_memory_orchestrator.application.workflows import ClosedSessionPipelineWorkflow

    runner = _Pipeline()

    assert ClosedSessionPipelineWorkflow(runner).run_once(lease_seconds=42) == {"lease_seconds": 42}


def test_active_session_context_workflow_delegates() -> None:
    from agent_memory_orchestrator.application.workflows import ActiveSessionContextWorkflow

    result = ActiveSessionContextWorkflow(_Memory()).build(
        "why",
        session_id="s1",
        budget_tokens=100,
        limit=3,
        include_historical=True,
    )

    assert result == {
        "query": "why",
        "session_id": "s1",
        "budget_tokens": 100,
        "limit": 3,
        "include_historical": True,
    }


def test_peer_context_request_workflow_delegates() -> None:
    from agent_memory_orchestrator.application.workflows import PeerContextRequestWorkflow

    workflow = PeerContextRequestWorkflow(_PeerAgent())

    assert workflow.ask("why", peer_ids=["p1"], session_id="s1", min_confidence=0.7, timeout_seconds=1) == {
        "query": "why",
        "peer_ids": ["p1"],
        "session_id": "s1",
        "min_confidence": 0.7,
        "timeout_seconds": 1,
    }
    assert workflow.context("room1") == {"room_id": "room1"}


def test_connector_ingestion_workflow_delegates() -> None:
    from agent_memory_orchestrator.application.workflows import ConnectorIngestionWorkflow

    workflow = ConnectorIngestionWorkflow(_Connector())

    assert workflow.ingest({"event": "message"}) == {"envelope": {"event": "message"}}
    assert workflow.finalize(session_id="s1", reason="idle", message_count=2) == {
        "session_id": "s1",
        "reason": "idle",
        "message_count": 2,
    }


class _Pipeline:
    def run_next(self, *, lease_seconds: int = 300) -> dict[str, int]:
        return {"lease_seconds": lease_seconds}


class _Memory:
    def build_context_pack(
        self,
        query: str,
        session_id: str | None = None,
        budget_tokens: int | None = None,
        limit: int = 12,
        *,
        include_historical: bool = False,
    ) -> dict[str, object]:
        return {
            "query": query,
            "session_id": session_id,
            "budget_tokens": budget_tokens,
            "limit": limit,
            "include_historical": include_historical,
        }


class _PeerAgent:
    def ask(
        self,
        *,
        query: str,
        peer_ids: list[str] | None = None,
        session_id: str = "",
        min_confidence: float | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        return {
            "query": query,
            "peer_ids": peer_ids,
            "session_id": session_id,
            "min_confidence": min_confidence,
            "timeout_seconds": timeout_seconds,
        }

    def context(self, room_id: str) -> dict[str, str]:
        return {"room_id": room_id}


class _Connector:
    def handle_event_envelope(self, envelope: dict[str, object]) -> dict[str, object]:
        return {"envelope": envelope}

    def finalize_session(self, *, session_id: str, reason: str = "idle_timeout", message_count: int = 0) -> dict[str, object]:
        return {"session_id": session_id, "reason": reason, "message_count": message_count}
