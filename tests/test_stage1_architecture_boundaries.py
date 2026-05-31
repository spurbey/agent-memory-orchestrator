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


def test_stage1_retrieval_boundary_exports_planned_module_names() -> None:
    from agent_memory_orchestrator.domain.retrieval import build_answer_trace
    from agent_memory_orchestrator.domain.retrieval import build_central_answer_trace
    from agent_memory_orchestrator.domain.retrieval import classify_query
    from agent_memory_orchestrator.domain.retrieval import format_answer_trace
    from agent_memory_orchestrator.domain.retrieval.intent import query_has_code_locator

    assert classify_query("why did graph_service.py change?") == "code_why"
    assert query_has_code_locator("graph_service.py") is True
    assert build_answer_trace.__name__ == "build_answer_trace"
    assert build_central_answer_trace.__name__ == "build_central_answer_trace"
    assert format_answer_trace.__name__ == "format_answer_trace"
