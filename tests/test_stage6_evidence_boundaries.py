from __future__ import annotations

from agent_memory_orchestrator.evidence import EvidenceDrain as LegacyEvidenceDrain
from agent_memory_orchestrator.evidence import RawEvidenceRef as LegacyRawEvidenceRef
from agent_memory_orchestrator.evidence import TriggerDecision as LegacyTriggerDecision
from agent_memory_orchestrator.evidence import session_boundary_trigger as legacy_session_boundary_trigger


def test_domain_evidence_boundary_exports_existing_contracts() -> None:
    from agent_memory_orchestrator.domain.evidence import EvidenceDrain
    from agent_memory_orchestrator.domain.evidence import RawEvidenceRef
    from agent_memory_orchestrator.domain.evidence import TriggerDecision
    from agent_memory_orchestrator.domain.evidence import session_boundary_trigger

    assert EvidenceDrain is LegacyEvidenceDrain
    assert RawEvidenceRef is LegacyRawEvidenceRef
    assert TriggerDecision is LegacyTriggerDecision
    assert session_boundary_trigger is legacy_session_boundary_trigger


def test_application_evidence_services_delegate_without_new_behavior(tmp_path) -> None:
    from agent_memory_orchestrator.application.services import EvidenceIngestService
    from agent_memory_orchestrator.application.services import SessionBoundaryService

    ref = EvidenceIngestService(tmp_path).append(
        {"hello": "world"},
        session_id="session:stage6",
        source_app="codex",
        event_name="user_prompt",
    )

    assert ref.session_id == "session:stage6"
    assert ref.event_name == "user_prompt"
    assert (tmp_path / f"{ref.created_at[:10]}.jsonl").exists()

    fake = _FakeDrain()
    service = SessionBoundaryService(fake)  # type: ignore[arg-type]

    assert service.drain_closed_sessions(limit=1)["limit"] == 1
    assert service.pending(session_id="s1") == {"session_id": "s1"}


class _FakeDrain:
    def drain(self, *, limit: int = 500, session_id: str = "", max_windows: int | None = None) -> dict[str, object]:
        return {"limit": limit, "session_id": session_id, "max_windows": max_windows}

    def pending(self, *, session_id: str = "") -> dict[str, object]:
        return {"session_id": session_id}
