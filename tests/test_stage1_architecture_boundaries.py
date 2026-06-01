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

    assert CodeHunk.__name__ == "CodeHunk"
    assert CodeNode.__name__ == "CodeNode"
    assert AstExpansion.__name__ == "AstExpansion"
    assert parse_unified_zero_hunks.__name__ == "parse_unified_zero_hunks"
    assert HunkBoundary is CodeHunk
    assert VersionBoundary is CodeVersionPlan
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


def test_stage1_domain_code_analysis_is_compatibility_only() -> None:
    analysis_path = (
        Path(__file__).resolve().parents[1] / "src" / "agent_memory_orchestrator" / "domain" / "code" / "analysis.py"
    )
    tree = ast.parse(analysis_path.read_text(encoding="utf-8-sig"))
    class_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    function_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)]

    assert class_names == []
    assert function_names == []
    for node in tree.body:
        if _is_module_docstring(node) or _is_future_annotations_import(node):
            continue
        if isinstance(node, ast.ImportFrom):
            continue
        assert _is_all_assignment(node)


def test_stage1_domain_code_package_roots_are_export_only() -> None:
    code_root = Path(__file__).resolve().parents[1] / "src" / "agent_memory_orchestrator" / "domain" / "code"
    package_roots = [
        code_root / "ast" / "__init__.py",
        code_root / "diff" / "__init__.py",
        code_root / "hunks" / "__init__.py",
    ]

    for path in package_roots:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        class_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        function_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)]
        assert class_names == [], path
        assert function_names == [], path
        for node in tree.body:
            if _is_module_docstring(node) or _is_future_annotations_import(node):
                continue
            if isinstance(node, ast.ImportFrom):
                continue
            assert _is_all_assignment(node), path


def test_stage1_application_and_infrastructure_boundaries_are_importable() -> None:
    from agent_memory_orchestrator.application.services import CentralMergeService as RootCentralMergeService
    from agent_memory_orchestrator.application.services import ProductionPipelineService as RootProductionPipelineService
    from agent_memory_orchestrator.application.services import RetrievalQueryService as RootRetrievalQueryService
    from agent_memory_orchestrator.application.services.capture import EvidenceIngestService
    from agent_memory_orchestrator.application.services.central_merge import CentralMergeService
    from agent_memory_orchestrator.application.services.connectors import ConnectorRuntimeService
    from agent_memory_orchestrator.application.services.memory_graph.service import GraphRagService
    from agent_memory_orchestrator.application.pipeline import build_compact_session_graph
    from agent_memory_orchestrator.application.pipeline import build_curated_session_graph
    from agent_memory_orchestrator.application.services.peer import PeerAgentService
    from agent_memory_orchestrator.application.services.pipeline import ProductionPipelineService
    from agent_memory_orchestrator.application.services.retrieval import RetrievalQueryService
    from agent_memory_orchestrator.application.services.retrieval import retrieve_session_graph
    from agent_memory_orchestrator.application.services.review import LocalAgentReviewService
    from agent_memory_orchestrator.application.services.session import build_session_detail_fallback
    from agent_memory_orchestrator.domain.evidence.events import HOOK_CONTEXT_EVENTS
    from agent_memory_orchestrator.domain.evidence import build_reasoning_evidence_view
    from agent_memory_orchestrator.domain.reasoning import TimelineGraph
    from agent_memory_orchestrator.domain.reasoning import build_decision_threads
    from agent_memory_orchestrator.domain.reasoning import extract_decisions
    from agent_memory_orchestrator.domain.versioning.flow import VERSION_FLOW_EDGE_KINDS
    from agent_memory_orchestrator.domain.versioning import resolve_session_repo_root
    from agent_memory_orchestrator.infrastructure.faiss import GraphEmbeddingStore
    from agent_memory_orchestrator.infrastructure.kuzu import KuzuGraphStore
    from agent_memory_orchestrator.infrastructure.sqlite import ProductionSessionJobStore
    from agent_memory_orchestrator.infrastructure.sqlite import RetrievalIndexStore

    assert RootProductionPipelineService is ProductionPipelineService
    assert RootCentralMergeService is CentralMergeService
    assert RootRetrievalQueryService is RetrievalQueryService
    assert ProductionPipelineService.__name__ == "ProductionPipelineService"
    assert CentralMergeService.__name__ == "CentralMergeService"
    assert ConnectorRuntimeService.__name__ == "ConnectorRuntimeService"
    assert EvidenceIngestService.__name__ == "EvidenceIngestService"
    assert LocalAgentReviewService.__name__ == "LocalAgentReviewService"
    assert PeerAgentService.__name__ == "PeerAgentService"
    assert RetrievalQueryService.__name__ == "RetrievalQueryService"
    assert GraphRagService.__name__ == "GraphRagService"
    assert retrieve_session_graph.__name__ == "retrieve_session_graph"
    assert build_session_detail_fallback.__name__ == "build_session_detail_fallback"
    assert ProductionSessionJobStore.__name__ == "ProductionSessionJobStore"
    assert RetrievalIndexStore.__name__ == "RetrievalIndexStore"
    assert KuzuGraphStore.__name__ == "KuzuGraphStore"
    assert GraphEmbeddingStore.__name__ == "GraphEmbeddingStore"
    assert build_compact_session_graph.__name__ == "build_compact_session_graph"
    assert build_curated_session_graph.__name__ == "build_curated_session_graph"
    assert build_reasoning_evidence_view.__name__ == "build_reasoning_evidence_view"
    assert TimelineGraph.__name__ == "TimelineGraph"
    assert build_decision_threads.__name__ == "build_decision_threads"
    assert extract_decisions.__name__ == "extract_decisions"
    assert resolve_session_repo_root.__name__ == "resolve_session_repo_root"
    assert "session_start" in HOOK_CONTEXT_EVENTS
    assert "COMMITTED_AS" in VERSION_FLOW_EDGE_KINDS


def test_stage1_retrieval_boundary_exports_planned_module_names() -> None:
    from agent_memory_orchestrator.domain.retrieval import build_answer_trace
    from agent_memory_orchestrator.domain.retrieval import build_central_answer_trace
    from agent_memory_orchestrator.domain.retrieval import classify_query
    from agent_memory_orchestrator.domain.retrieval import format_answer_trace
    from agent_memory_orchestrator.domain.retrieval.constants import ANSWER_SEED_KINDS
    from agent_memory_orchestrator.domain.retrieval.intent import query_has_code_locator
    from agent_memory_orchestrator.domain.retrieval.policy import _rank_nodes

    assert classify_query("why did graph_service.py change?") == "code_why"
    assert query_has_code_locator("graph_service.py") is True
    assert build_answer_trace.__name__ == "build_answer_trace"
    assert build_central_answer_trace.__name__ == "build_central_answer_trace"
    assert format_answer_trace.__name__ == "format_answer_trace"
    assert "KnowledgeVersion" in ANSWER_SEED_KINDS
    assert _rank_nodes.__name__ == "_rank_nodes"


def test_stage1_production_code_does_not_depend_on_legacy_reasoning_graph_facades() -> None:
    src_root = Path(__file__).resolve().parents[1] / "src" / "agent_memory_orchestrator"
    forbidden = (
        "agent_memory_orchestrator.reasoning_graph.jobs",
        "agent_memory_orchestrator.reasoning_graph.central_merge",
        "agent_memory_orchestrator.reasoning_graph.chunking",
        "agent_memory_orchestrator.reasoning_graph.code_analysis",
        "agent_memory_orchestrator.reasoning_graph.code_versioning",
        "agent_memory_orchestrator.reasoning_graph.decision_extraction",
        "agent_memory_orchestrator.reasoning_graph.decision_quality",
        "agent_memory_orchestrator.reasoning_graph.decision_packets",
        "agent_memory_orchestrator.reasoning_graph.embedding_store",
        "agent_memory_orchestrator.reasoning_graph.evidence_view",
        "agent_memory_orchestrator.reasoning_graph.promotion",
        "agent_memory_orchestrator.reasoning_graph.retrieval",
        "agent_memory_orchestrator.reasoning_graph.reasoning_extraction",
        "agent_memory_orchestrator.reasoning_graph.relationships",
        "agent_memory_orchestrator.reasoning_graph.repo_resolution",
        "agent_memory_orchestrator.reasoning_graph.session_graph_writer",
        "agent_memory_orchestrator.reasoning_graph.session_runtime",
        "agent_memory_orchestrator.reasoning_graph.stage4_contract",
        "agent_memory_orchestrator.reasoning_graph.timeline",
        "agent_memory_orchestrator.reasoning_graph.validation",
        "agent_memory_orchestrator.reasoning_graph.work_packets",
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


def test_stage1_graph_root_is_compatibility_only() -> None:
    src_root = Path(__file__).resolve().parents[1] / "src" / "agent_memory_orchestrator"
    graph_root = src_root / "graph"
    allowed_functions = {
        "__init__.py": {"__getattr__"},
        "text_utils.py": {"_clip"},
    }

    for path in graph_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        class_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        function_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
        assert class_names == [], path.name
        assert set(function_names) <= allowed_functions.get(path.name, set()), path.name

    offenders: list[str] = []
    for path in src_root.rglob("*.py"):
        relative = path.relative_to(src_root).as_posix()
        if relative.startswith("graph/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [_absolute_or_suffix_module(node.module or "")]
            else:
                continue
            if any(module == "agent_memory_orchestrator.graph" or module.startswith("agent_memory_orchestrator.graph.") for module in modules):
                offenders.append(relative)
                break

    assert offenders == []


def test_stage1_source_roots_have_explicit_product_ownership() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    src_root = repo_root / "src" / "agent_memory_orchestrator"
    actual_roots = {path.name for path in src_root.iterdir() if path.is_dir() and path.name != "__pycache__"}
    product_domain_roots = {"domain", "evidence", "versioning", "peer"}
    product_application_roots = {"application"}
    infrastructure_roots = {"infrastructure", "llm", "integrations", "install", "bin"}
    runtime_roots = {"runtime", "web", "skill_checkpoint", "core", "orchestration", "extensions"}
    compatibility_roots = {"graph"}
    legacy_public_roots = {"memory", "retrieval"}
    expected_roots = (
        product_domain_roots
        | product_application_roots
        | infrastructure_roots
        | runtime_roots
        | compatibility_roots
        | legacy_public_roots
    )

    assert actual_roots == expected_roots
    assert not (src_root / "reasoning_graph").exists()

    architecture_tree = (repo_root / "docs" / "ARCHITECTURE_TREE.md").read_text(encoding="utf-8")
    for root in expected_roots | {"reasoning_graph"}:
        assert f"`{root}/`" in architecture_tree


def test_stage1_application_services_root_is_compatibility_only() -> None:
    services_root = Path(__file__).resolve().parents[1] / "src" / "agent_memory_orchestrator" / "application" / "services"
    implementation_packages = {
        "capture",
        "central_merge",
        "connectors",
        "memory_graph",
        "peer",
        "pipeline",
        "retrieval",
        "review",
        "session",
    }

    assert {path.name for path in services_root.iterdir() if path.is_dir()} >= implementation_packages

    for path in services_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        class_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        function_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)]
        if path.name == "__init__.py":
            assert class_names == []
            assert set(function_names) <= {"__getattr__"}
            continue

        assert class_names == [], path.name
        assert function_names == [], path.name
        for node in tree.body:
            if _is_module_docstring(node) or _is_future_annotations_import(node):
                continue
            if isinstance(node, ast.ImportFrom):
                continue
            assert _is_all_assignment(node), path.name


def test_stage1_central_merge_apply_is_orchestration_only() -> None:
    apply_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "agent_memory_orchestrator"
        / "application"
        / "services"
        / "central_merge"
        / "apply.py"
    )
    tree = ast.parse(apply_path.read_text(encoding="utf-8-sig"))
    class_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    function_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)]

    assert class_names == []
    assert set(function_names) == {"apply_merge_plan"}


def test_stage1_session_graph_runtime_is_compatibility_only() -> None:
    runtime_path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "agent_memory_orchestrator"
        / "application"
        / "services"
        / "session"
        / "graph_runtime.py"
    )
    tree = ast.parse(runtime_path.read_text(encoding="utf-8-sig"))
    class_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    function_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)]

    assert class_names == []
    assert function_names == []


def _absolute_or_suffix_module(module: str) -> str:
    if module.startswith("agent_memory_orchestrator."):
        return module
    return f"agent_memory_orchestrator.{module}"


def _is_forbidden_legacy_import(module: str, forbidden: tuple[str, ...]) -> bool:
    return any(module == blocked or module.startswith(f"{blocked}.") for blocked in forbidden)


def _is_module_docstring(node: ast.AST) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def _is_future_annotations_import(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
    )


def _is_all_assignment(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "__all__"
    )
