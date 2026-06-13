from __future__ import annotations

import ast
from pathlib import Path


def test_cli_boundary_groups_are_importable() -> None:
    from agent_memory_orchestrator.runtime.cli.commands.connectors import SLACK_SUBCOMMANDS
    from agent_memory_orchestrator.runtime.cli.commands.graph import GRAPH_COMMANDS
    from agent_memory_orchestrator.runtime.cli.commands.models import MODEL_SUBCOMMANDS
    from agent_memory_orchestrator.runtime.cli.commands.peer import add_peer_subcommands
    from agent_memory_orchestrator.runtime.cli.commands.peer import handle_peer_command
    from agent_memory_orchestrator.runtime.cli.commands.peer import peer_netd_options_from_args
    from agent_memory_orchestrator.runtime.cli.commands.pipeline import PRODUCTION_SUBCOMMANDS
    from agent_memory_orchestrator.runtime.cli.commands.retrieval import RETRIEVAL_COMMANDS

    assert "merge-apply" in PRODUCTION_SUBCOMMANDS
    assert "graph-retrieve" in RETRIEVAL_COMMANDS
    assert "graph-status" in GRAPH_COMMANDS
    assert "status" in SLACK_SUBCOMMANDS
    assert "preflight" in MODEL_SUBCOMMANDS
    assert callable(add_peer_subcommands)
    assert callable(handle_peer_command)
    assert callable(peer_netd_options_from_args)


def test_daemon_route_boundary_groups_are_importable() -> None:
    from agent_memory_orchestrator.runtime.daemon.routes.connectors import CONNECTOR_ROUTES
    from agent_memory_orchestrator.runtime.daemon.routes.graph import GRAPH_ROUTES
    from agent_memory_orchestrator.runtime.daemon.routes.health import HEALTH_ROUTES
    from agent_memory_orchestrator.runtime.daemon.routes.hooks import HOOK_ROUTES
    from agent_memory_orchestrator.runtime.daemon.routes.jobs import JOB_ROUTES
    from agent_memory_orchestrator.runtime.daemon.routes.retrieval import RETRIEVAL_ROUTES

    assert "/health" in HEALTH_ROUTES
    assert "/api/jobs" in JOB_ROUTES
    assert "/api/graph/status" in GRAPH_ROUTES
    assert "/graph/retrieve" in RETRIEVAL_ROUTES
    assert "/api/connectors/slack/status" in CONNECTOR_ROUTES
    assert "/hooks/ingest" in HOOK_ROUTES


def test_mcp_tool_boundary_groups_are_importable() -> None:
    from agent_memory_orchestrator.runtime.mcp.tools.graph import GRAPH_TOOL_NAMES
    from agent_memory_orchestrator.runtime.mcp.tools.graph_service import GraphToolMixin
    from agent_memory_orchestrator.runtime.mcp.tools.memory import MEMORY_TOOL_NAMES
    from agent_memory_orchestrator.runtime.mcp.tools.memory_service import MemoryToolMixin
    from agent_memory_orchestrator.runtime.mcp.tools.orchestration import ORCHESTRATION_TOOL_NAMES
    from agent_memory_orchestrator.runtime.mcp.tools.peer import PEER_TOOL_NAMES
    from agent_memory_orchestrator.runtime.mcp.tools.peer_service import PeerToolMixin
    from agent_memory_orchestrator.runtime.mcp.tools.retrieval import RETRIEVAL_TOOL_NAMES
    from agent_memory_orchestrator.runtime.mcp.tools.semantic_harness import SEMANTIC_HARNESS_TOOL_NAMES
    from agent_memory_orchestrator.runtime.mcp.tools.semantic_harness_service import SemanticHarnessToolMixin
    from agent_memory_orchestrator.runtime.mcp.tools.service import MemoryMcpToolService

    assert "memory_search" in MEMORY_TOOL_NAMES
    assert "amo_graph_search" in GRAPH_TOOL_NAMES
    assert "amo_graph_search" in RETRIEVAL_TOOL_NAMES
    assert "amo_harness_query" in SEMANTIC_HARNESS_TOOL_NAMES
    assert "peer_memory_ask" in PEER_TOOL_NAMES
    assert "orchestrator_start" in ORCHESTRATION_TOOL_NAMES
    assert issubclass(MemoryMcpToolService, MemoryToolMixin)
    assert issubclass(MemoryMcpToolService, GraphToolMixin)
    assert issubclass(MemoryMcpToolService, PeerToolMixin)
    assert issubclass(MemoryMcpToolService, SemanticHarnessToolMixin)


def test_mcp_tools_package_root_is_export_only() -> None:
    path = Path(__file__).resolve().parents[1] / "src" / "agent_memory_orchestrator" / "runtime" / "mcp" / "tools" / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
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


def test_peer_cli_package_root_is_export_only() -> None:
    path = Path(__file__).resolve().parents[1] / "src" / "agent_memory_orchestrator" / "runtime" / "cli" / "commands" / "peer" / "__init__.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
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


def test_runtime_web_boundary_delegates_to_existing_asset_loader() -> None:
    from agent_memory_orchestrator.runtime.daemon import web_assets as daemon_assets
    from agent_memory_orchestrator.runtime.web import assets as runtime_assets

    assert runtime_assets.load_web_asset is daemon_assets.load_web_asset
    assert runtime_assets.web_asset_bytes is daemon_assets.web_asset_bytes
    assert "AMO Control Room" in runtime_assets.session_cockpit_html()


def test_production_eval_facade_keeps_specialized_boundaries() -> None:
    from agent_memory_orchestrator.application.pipeline.evaluation import production_eval
    from agent_memory_orchestrator.application.pipeline.evaluation.production_eval_cases import _cases
    from agent_memory_orchestrator.application.pipeline.evaluation.production_eval_retrieval import _faiss_state
    from agent_memory_orchestrator.application.pipeline.evaluation.production_eval_storage import stable_hash

    assert production_eval._cases is _cases
    assert production_eval._faiss_state is _faiss_state
    assert production_eval.stable_hash is stable_hash


def test_kuzu_graph_store_facade_keeps_specialized_boundaries() -> None:
    from agent_memory_orchestrator.infrastructure.kuzu.graph_store import GraphNode
    from agent_memory_orchestrator.infrastructure.kuzu.graph_store import InMemoryGraphStore
    from agent_memory_orchestrator.infrastructure.kuzu.graph_store import KuzuGraphStore
    from agent_memory_orchestrator.infrastructure.kuzu.graph_store.kuzu import KuzuGraphStore as KuzuGraphStoreImpl
    from agent_memory_orchestrator.infrastructure.kuzu.graph_store.memory import InMemoryGraphStore as InMemoryGraphStoreImpl
    from agent_memory_orchestrator.infrastructure.kuzu.graph_store.models import GraphNode as GraphNodeModel

    assert GraphNode is GraphNodeModel
    assert InMemoryGraphStore is InMemoryGraphStoreImpl
    assert KuzuGraphStore is KuzuGraphStoreImpl


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
