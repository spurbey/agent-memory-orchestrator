from __future__ import annotations


def test_stage1_domain_code_boundary_exports_existing_code_contracts() -> None:
    from agent_memory_orchestrator.domain.code import AstExpansion
    from agent_memory_orchestrator.domain.code import CodeHunk
    from agent_memory_orchestrator.domain.code import CodeNode
    from agent_memory_orchestrator.domain.code import parse_unified_zero_hunks

    assert CodeHunk.__name__ == "CodeHunk"
    assert CodeNode.__name__ == "CodeNode"
    assert AstExpansion.__name__ == "AstExpansion"
    assert parse_unified_zero_hunks.__name__ == "parse_unified_zero_hunks"


def test_stage1_domain_reasoning_boundary_exports_existing_reasoning_contracts() -> None:
    from agent_memory_orchestrator.domain.reasoning import ReasoningExtractionReview
    from agent_memory_orchestrator.domain.reasoning import ReasoningWorkPacketBuild
    from agent_memory_orchestrator.domain.reasoning import build_stage4_packet_prompt
    from agent_memory_orchestrator.domain.reasoning import is_strict_validation_fact

    assert ReasoningExtractionReview.__name__ == "ReasoningExtractionReview"
    assert ReasoningWorkPacketBuild.__name__ == "ReasoningWorkPacketBuild"
    assert build_stage4_packet_prompt.__name__ == "build_stage4_packet_prompt"
    assert is_strict_validation_fact({"command": "python -m pytest -q"}) is True


def test_stage1_application_and_infrastructure_boundaries_are_importable() -> None:
    from agent_memory_orchestrator.application.services import CentralMergeService
    from agent_memory_orchestrator.application.services import ProductionPipelineService
    from agent_memory_orchestrator.application.services import RetrievalQueryService
    from agent_memory_orchestrator.infrastructure.faiss import GraphEmbeddingStore
    from agent_memory_orchestrator.infrastructure.kuzu import KuzuGraphStore
    from agent_memory_orchestrator.infrastructure.sqlite import ProductionSessionJobStore
    from agent_memory_orchestrator.infrastructure.sqlite import RetrievalIndexStore

    assert ProductionPipelineService.__name__ == "ProductionPipelineService"
    assert CentralMergeService.__name__ == "CentralMergeService"
    assert RetrievalQueryService.__name__ == "RetrievalQueryService"
    assert ProductionSessionJobStore.__name__ == "ProductionSessionJobStore"
    assert RetrievalIndexStore.__name__ == "RetrievalIndexStore"
    assert KuzuGraphStore.__name__ == "KuzuGraphStore"
    assert GraphEmbeddingStore.__name__ == "GraphEmbeddingStore"
