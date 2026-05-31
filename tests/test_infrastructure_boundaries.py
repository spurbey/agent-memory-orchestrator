from __future__ import annotations


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
