from __future__ import annotations

import ast
from pathlib import Path


def test_git_infrastructure_exports_existing_backend_contracts() -> None:
    from agent_memory_orchestrator.infrastructure.git import LocalGitBackend
    from agent_memory_orchestrator.infrastructure.git import git_unified_zero_diff
    from agent_memory_orchestrator.versioning import LocalGitBackend as LegacyLocalGitBackend
    from agent_memory_orchestrator.domain.code.diff import git_unified_zero_diff as domain_git_unified_zero_diff

    assert LocalGitBackend is LegacyLocalGitBackend
    assert git_unified_zero_diff is domain_git_unified_zero_diff


def test_llm_infrastructure_exports_existing_model_contracts() -> None:
    from agent_memory_orchestrator.infrastructure.llm import OllamaQwenClient
    from agent_memory_orchestrator.infrastructure.llm import RerankCandidate
    from agent_memory_orchestrator.infrastructure.llm import embed_text
    from agent_memory_orchestrator.llm import OllamaQwenClient as LegacyOllamaQwenClient
    from agent_memory_orchestrator.llm import RerankCandidate as LegacyRerankCandidate

    assert OllamaQwenClient is LegacyOllamaQwenClient
    assert RerankCandidate is LegacyRerankCandidate
    assert embed_text("hello", 4)


def test_llm_root_is_compatibility_over_infrastructure() -> None:
    from agent_memory_orchestrator.infrastructure.llm.models import resolve_models
    from agent_memory_orchestrator.infrastructure.llm.qwen import OllamaQwenClient
    from agent_memory_orchestrator.llm.models import resolve_models as legacy_resolve_models
    from agent_memory_orchestrator.llm.qwen import OllamaQwenClient as LegacyOllamaQwenClient

    assert LegacyOllamaQwenClient is OllamaQwenClient
    assert legacy_resolve_models is resolve_models


def test_llm_root_modules_are_export_only() -> None:
    llm_root = Path(__file__).resolve().parents[1] / "src" / "agent_memory_orchestrator" / "llm"

    for path in llm_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        class_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        function_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)]

        assert class_names == [], path.name
        assert function_names == [], path.name


def test_filesystem_infrastructure_helpers_are_deterministic(tmp_path) -> None:
    from agent_memory_orchestrator.infrastructure.filesystem import file_sha256
    from agent_memory_orchestrator.infrastructure.filesystem import path_hash
    from agent_memory_orchestrator.infrastructure.filesystem import read_jsonl_records
    from agent_memory_orchestrator.infrastructure.filesystem import timestamped_backup_path
    from agent_memory_orchestrator.infrastructure.filesystem import write_jsonl_records

    path = tmp_path / "events.jsonl"
    write_jsonl_records(path, [{"b": 2, "a": 1}])

    assert read_jsonl_records(path) == [{"a": 1, "b": 2}]
    assert file_sha256(path) == file_sha256(path)
    assert path_hash(tmp_path) == path_hash(tmp_path)
    assert timestamped_backup_path(path, timestamp="20260531T000000Z").name == "events.jsonl.backup-20260531T000000Z"


def test_sqlite_production_job_store_is_facade_over_concern_modules() -> None:
    from agent_memory_orchestrator.infrastructure.sqlite.production_job_store import ProductionSessionJobStore
    from agent_memory_orchestrator.infrastructure.sqlite.production_jobs import CentralMergeStoreMixin
    from agent_memory_orchestrator.infrastructure.sqlite.production_jobs import SemanticEvalStoreMixin
    from agent_memory_orchestrator.infrastructure.sqlite.production_jobs import SessionJobStoreMixin

    path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "agent_memory_orchestrator"
        / "infrastructure"
        / "sqlite"
        / "production_job_store.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    class_defs = [node for node in tree.body if isinstance(node, ast.ClassDef)]
    store_class = next(node for node in class_defs if node.name == "ProductionSessionJobStore")
    method_names = [node.name for node in store_class.body if isinstance(node, ast.FunctionDef)]

    assert issubclass(ProductionSessionJobStore, SessionJobStoreMixin)
    assert issubclass(ProductionSessionJobStore, CentralMergeStoreMixin)
    assert issubclass(ProductionSessionJobStore, SemanticEvalStoreMixin)
    assert method_names == ["__init__", "close"]
