from __future__ import annotations

import json

from agent_memory_orchestrator.application.services.semantic_harness.enrichment import (
    attach_agent_checkpoint_review,
)
from agent_memory_orchestrator.application.services.semantic_harness.enrichment import (
    ingest_agent_semantic_checkpoint,
)
from agent_memory_orchestrator.domain.semantic_harness import HarnessNode
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness.projection import build_projection_documents
from agent_memory_orchestrator.domain.semantic_harness.query_modes import answer_context_for_anchor
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import AGENT_CHECKPOINT_SCHEMA_VERSION
from agent_memory_orchestrator.domain.semantic_harness.store import InMemoryHarnessGraphStore


def test_checkpoint_file_writes_pending_review_artifacts_without_graph_mutation(tmp_path) -> None:
    graph = _graph()
    checkpoint_file = tmp_path / "semantic_checkpoint.json"
    checkpoint_file.write_text(json.dumps(_checkpoint()), encoding="utf-8")

    result = ingest_agent_semantic_checkpoint(checkpoint_file=checkpoint_file, graph=graph)

    assert result.mode == "pending"
    assert result.attach_result is None
    assert len(result.review.accepted_facts) == 1
    assert (tmp_path / "semantic_checkpoint.json").exists()
    assert (tmp_path / "resolved_proposals.json").exists()
    assert (tmp_path / "review_result.json").exists()
    assert (tmp_path / "attach_plan.json").exists()
    assert json.loads((tmp_path / "attach_plan.json").read_text(encoding="utf-8"))["graph_mutated"] is False


def test_checkpoint_attach_mode_updates_graph_node_metadata(tmp_path) -> None:
    graph = _graph()
    store = InMemoryHarnessGraphStore.from_graph(graph)
    checkpoint_file = tmp_path / "semantic_checkpoint.json"
    checkpoint_file.write_text(json.dumps(_checkpoint()), encoding="utf-8")

    result = ingest_agent_semantic_checkpoint(
        checkpoint_file=checkpoint_file,
        graph=graph,
        store=store,
        mode="attach",
    )

    assert result.attach_result is not None
    assert result.attach_result.updated_node_ids == ("symbol:repo:test:src/auth.py:login:function",)
    node = store.get_node("symbol:repo:test:src/auth.py:login:function")
    assert node is not None
    assert node.metadata["semantic_facts"][0]["source_kind"] == "agent_session"


def test_attached_checkpoint_fact_can_surface_in_context_for_anchor(tmp_path) -> None:
    graph = _graph()
    store = InMemoryHarnessGraphStore.from_graph(graph)
    checkpoint_file = tmp_path / "semantic_checkpoint.json"
    checkpoint_file.write_text(json.dumps(_checkpoint()), encoding="utf-8")
    ingest_agent_semantic_checkpoint(checkpoint_file=checkpoint_file, graph=graph, store=store, mode="attach")

    result = answer_context_for_anchor(
        store.to_graph(),
        symbols=("src/auth.py::login",),
        questions=("why does this exist?",),
    )

    assert result.status == "ready"
    assert "anonymous sessions" in result.answers[0].answer


def test_attached_checkpoint_fact_builds_semantic_projection_document(tmp_path) -> None:
    graph = _graph()
    store = InMemoryHarnessGraphStore.from_graph(graph)
    checkpoint_file = tmp_path / "semantic_checkpoint.json"
    checkpoint_file.write_text(json.dumps(_checkpoint()), encoding="utf-8")
    ingest_agent_semantic_checkpoint(checkpoint_file=checkpoint_file, graph=graph, store=store, mode="attach")

    documents = build_projection_documents(store.to_graph())

    semantic_docs = [
        document
        for document in documents
        if document.metadata.get("projection_source") == "semantic_harness_semantic_fact"
    ]
    assert len(semantic_docs) == 1
    assert "anonymous sessions" in semantic_docs[0].text


def test_relationship_fact_with_unresolved_anchor_is_rejected(tmp_path) -> None:
    graph = _graph()
    payload = _checkpoint()
    fact = payload["work_windows"][0]["semantic_facts"][0]
    fact["fact_type"] = "relationship_reason"
    fact["text"] = "The auth and missing session modules must preserve anonymous-session behavior together."
    fact["anchors"] = [
        {"path": "src/auth.py", "symbol": "login", "line_start": 1, "line_end": 2},
        {"path": "src/missing_session.py"},
    ]
    checkpoint_file = tmp_path / "semantic_checkpoint.json"
    checkpoint_file.write_text(json.dumps(payload), encoding="utf-8")

    result = ingest_agent_semantic_checkpoint(checkpoint_file=checkpoint_file, graph=graph)

    assert len(result.review.rejected_facts) == 1
    assert any(item["reason"] == "unresolved_path" for item in result.diagnostics)
    assert any(item["reason"] == "relationship_fact_needs_two_anchors" for item in result.review.diagnostics)


def test_markdown_region_anchor_resolves_to_file_with_warning(tmp_path) -> None:
    graph = _graph()
    payload = _checkpoint()
    fact = payload["work_windows"][0]["semantic_facts"][0]
    fact["fact_type"] = "semantic_role"
    fact["text"] = "The checkpoint docs define the trust boundary between forked agent proposals and AMO graph truth."
    fact["anchors"] = [
        {
            "path": "docs/semantic_harness/integrations/agent-semantic-checkpoint.md",
            "code_region_hint": "trust boundary",
            "line_start": 5,
            "line_end": 20,
        }
    ]
    checkpoint_file = tmp_path / "semantic_checkpoint.json"
    checkpoint_file.write_text(json.dumps(payload), encoding="utf-8")

    result = ingest_agent_semantic_checkpoint(checkpoint_file=checkpoint_file, graph=graph)

    assert len(result.review.accepted_facts) == 1
    assert result.review.accepted_facts[0].anchor_node_ids == (
        "file:repo:test:docs/semantic_harness/integrations/agent-semantic-checkpoint.md",
    )
    assert any(item["reason"] == "region_anchor_resolved_to_file" for item in result.diagnostics)


def test_attach_command_path_can_use_pending_review_artifacts(tmp_path) -> None:
    graph = _graph()
    store = InMemoryHarnessGraphStore.from_graph(graph)
    checkpoint_file = tmp_path / "semantic_checkpoint.json"
    checkpoint_file.write_text(json.dumps(_checkpoint()), encoding="utf-8")
    pending = ingest_agent_semantic_checkpoint(checkpoint_file=checkpoint_file, graph=graph)
    attach_dir = tmp_path / "attach-artifacts"

    result = attach_agent_checkpoint_review(
        review_artifact=pending.artifacts_dir / "review_result.json",
        graph=graph,
        store=store,
        out_dir=attach_dir,
    )

    assert result.attach_result is not None
    assert result.attach_result.attached_fact_ids
    assert (attach_dir / "attach_plan.json").exists()


def test_multi_window_checkpoint_processes_each_window(tmp_path) -> None:
    graph = _graph()
    payload = _checkpoint()
    second = dict(payload["work_windows"][0])
    second["window_id"] = "window-2"
    second["semantic_facts"] = [dict(payload["work_windows"][0]["semantic_facts"][0])]
    second["semantic_facts"][0]["text"] = "The same login contract is validated after the second commit window."
    payload["work_windows"].append(second)
    checkpoint_file = tmp_path / "semantic_checkpoint.json"
    checkpoint_file.write_text(json.dumps(payload), encoding="utf-8")

    result = ingest_agent_semantic_checkpoint(checkpoint_file=checkpoint_file, graph=graph)

    assert len(result.proposals) == 2
    assert len(result.review.accepted_facts) == 2


def _graph() -> StructuralHarnessGraph:
    return StructuralHarnessGraph(
        repo_id="repo:test",
        nodes=(
            HarnessNode(id="repo:test", kind="Repo", label="repo:test", repo_id="repo:test"),
            HarnessNode(
                id="file:repo:test:src/auth.py",
                kind="File",
                label="src/auth.py",
                repo_id="repo:test",
                metadata={"path": "src/auth.py"},
            ),
            HarnessNode(
                id="symbol:repo:test:src/auth.py:login:function",
                kind="Symbol",
                label="login",
                repo_id="repo:test",
                metadata={
                    "path": "src/auth.py",
                    "qualified_name": "login",
                    "symbol_kind": "function",
                    "line_start": 1,
                    "line_end": 3,
                },
            ),
            HarnessNode(
                id="file:repo:test:docs/semantic_harness/integrations/agent-semantic-checkpoint.md",
                kind="File",
                label="docs/semantic_harness/integrations/agent-semantic-checkpoint.md",
                repo_id="repo:test",
                metadata={"path": "docs/semantic_harness/integrations/agent-semantic-checkpoint.md"},
            ),
        ),
        edges=(),
    )


def _checkpoint() -> dict[str, object]:
    return {
        "schema_version": AGENT_CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_id": "checkpoint-1",
        "parent_session_id": "session-1",
        "repo_root": "C:/repo",
        "base_commit": "abc",
        "head_commit": "def",
        "checkpoint_time": "2026-06-18T12:00:00Z",
        "session_goal": "Preserve auth semantics.",
        "work_windows": [
            {
                "window_id": "window-1",
                "commit_sha": "def",
                "commit_message": "Preserve login None behavior",
                "changed_files": ["src/auth.py"],
                "tests_run": [{"command": "pytest", "status": "passed", "excerpt": "1 passed"}],
                "semantic_facts": [
                    {
                        "fact_type": "implementation_rationale",
                        "text": "Login returns None so older route handlers can treat missing users as anonymous sessions.",
                        "anchors": [
                            {
                                "path": "src/auth.py",
                                "symbol": "login",
                                "line_start": 1,
                                "line_end": 2,
                                "anchor_confidence": 0.9,
                            }
                        ],
                        "source_refs": [
                            {
                                "kind": "diff",
                                "commit_sha": "def",
                                "path": "src/auth.py",
                                "line_start": 1,
                                "line_end": 2,
                                "excerpt": "return None",
                            }
                        ],
                        "derivability": "requires_agent_session_history",
                        "source_kind": "agent_session",
                        "source_span": "validated_committed",
                        "confidence": 0.84,
                    }
                ],
                "rejected_approaches": [],
                "open_questions": [],
            }
        ],
    }
