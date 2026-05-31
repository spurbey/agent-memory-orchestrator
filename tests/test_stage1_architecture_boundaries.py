from __future__ import annotations

import ast
from pathlib import Path


def test_stage1_domain_code_boundary_exports_existing_code_contracts() -> None:
    from agent_memory_orchestrator.domain.code import AstExpansion
    from agent_memory_orchestrator.domain.code import CodeHunk
    from agent_memory_orchestrator.domain.code import CodeNode
    from agent_memory_orchestrator.domain.code import CodeVersionPlan
    from agent_memory_orchestrator.domain.code import CodeVersionRecord
    from agent_memory_orchestrator.domain.code import SymbolRecord
    from agent_memory_orchestrator.domain.code import parse_unified_zero_hunks
    from agent_memory_orchestrator.domain.code import resolve_code_node_version
    from agent_memory_orchestrator.domain.code import symbol_key
    from agent_memory_orchestrator.domain.code.hunks import CodeHunk as HunkBoundary
    from agent_memory_orchestrator.domain.code.versions import CodeVersionPlan as VersionBoundary
    from agent_memory_orchestrator.reasoning_graph import CodeVersionPlan as LegacyCodeVersionPlan

    assert CodeHunk.__name__ == "CodeHunk"
    assert CodeNode.__name__ == "CodeNode"
    assert AstExpansion.__name__ == "AstExpansion"
    assert parse_unified_zero_hunks.__name__ == "parse_unified_zero_hunks"
    assert HunkBoundary is CodeHunk
    assert CodeVersionPlan is LegacyCodeVersionPlan
    assert VersionBoundary is LegacyCodeVersionPlan
    assert CodeVersionRecord(version_id="v1", symbol_id="s1", code_node_id="c1").as_dict()["version_id"] == "v1"
    assert SymbolRecord(symbol_id="s1", symbol_key="a.py::f", qualified_name="f").as_dict()["qualified_name"] == "f"
    assert resolve_code_node_version.__name__ == "resolve_code_node_version"
    assert symbol_key("a.py", "f") == "a.py::f"


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


def test_stage1_production_code_does_not_depend_on_legacy_reasoning_graph_facades() -> None:
    src_root = Path(__file__).resolve().parents[1] / "src" / "agent_memory_orchestrator"
    forbidden = (
        "agent_memory_orchestrator.reasoning_graph.jobs",
        "agent_memory_orchestrator.reasoning_graph.central_merge",
        "agent_memory_orchestrator.reasoning_graph.code_versioning",
        "agent_memory_orchestrator.reasoning_graph.embedding_store",
        "agent_memory_orchestrator.reasoning_graph.retrieval",
        "agent_memory_orchestrator.reasoning_graph.session_runtime",
        "agent_memory_orchestrator.reasoning_graph.stage4_contract",
    )
    offenders: list[str] = []

    for path in src_root.rglob("*.py"):
        relative = path.relative_to(src_root).as_posix()
        if relative.startswith("reasoning_graph/"):
            continue
        text = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [_absolute_or_suffix_module(node.module or "")]
            else:
                continue
            if any(_is_forbidden_legacy_import(module, forbidden) for module in modules):
                offenders.append(relative)
                break

    assert offenders == []


def _absolute_or_suffix_module(module: str) -> str:
    if module.startswith("agent_memory_orchestrator."):
        return module
    return f"agent_memory_orchestrator.{module}"


def _is_forbidden_legacy_import(module: str, forbidden: tuple[str, ...]) -> bool:
    return any(module == blocked or module.startswith(f"{blocked}.") for blocked in forbidden)
