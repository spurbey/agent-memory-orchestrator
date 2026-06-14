from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryRequest
from agent_memory_orchestrator.domain.semantic_harness import SourceFile
from agent_memory_orchestrator.domain.semantic_harness import answer_structural_query
from agent_memory_orchestrator.domain.semantic_harness import build_structural_graph
from agent_memory_orchestrator.domain.semantic_harness import query as query_module


def test_query_accepts_public_skill_intents_without_downgrade() -> None:
    graph = build_structural_graph(
        "repo:test",
        (SourceFile(path="src/auth/session.py", text="def refresh_token():\n    return True\n"),),
    )

    for intent in ("edit_plan", "file_context", "tool_overlay", "impact_check", "test_plan", "why_changed"):
        response = answer_structural_query(
            graph,
            HarnessQueryRequest(
                intent=intent,
                user_goal="fix redirect token refresh",
                files=("src/auth/session.py",),
                max_cards=1,
                session_id="s1",
            ),
        )

        assert response.intent_requested == intent
        assert response.intent_used == intent
        assert not any(warning.startswith("unsupported_intent:") for warning in response.warnings)
        assert response.cards


def test_query_still_downgrades_unknown_intent_to_safe_file_context() -> None:
    graph = build_structural_graph(
        "repo:test",
        (SourceFile(path="src/auth/session.py", text="def refresh_token():\n    return True\n"),),
    )

    response = answer_structural_query(
        graph,
        HarnessQueryRequest(
            intent="custom_rewrite",
            user_goal="fix redirect token refresh",
            files=("src/auth/session.py",),
            max_cards=1,
            session_id="s1",
        ),
    )

    assert response.intent_requested == "custom_rewrite"
    assert response.intent_used == "file_context"
    assert "unsupported_intent:custom_rewrite" in response.warnings
    assert response.cards


def test_query_uses_lexical_projection_candidates_for_vague_goal_without_anchors() -> None:
    graph = build_structural_graph(
        "repo:test",
        (
            SourceFile(
                path="src/auth/session.py",
                text='''def refresh_token():\n    """Refresh token before redirect handling."""\n    return True\n''',
            ),
            SourceFile(path="README.md", text="# Auth\n\nUse src/auth/session.py for refresh_token redirect behavior.\n"),
        ),
    )

    response = answer_structural_query(
        graph,
        HarnessQueryRequest(
            intent="edit_plan",
            user_goal="fix redirect token refresh",
            max_cards=2,
            session_id="s1",
        ),
    )

    assert response.status == "partial_structural"
    assert response.cards[0].type == "next_file"
    assert response.cards[0].title == "Inspect src/auth/session.py"
    assert response.cards[0].evidence[1]["kind"] == "ProjectionDocumentAggregate"
    assert response.cards[0].evidence[1]["retrieval_source"] == "lexical_file_aggregate"
    assert "candidate_discovery:lexical_projection" in response.warnings
    assert "structural_only:no_work_history_or_semantic_reasoning_attached" in response.warnings
    assert response.next_actions[0].target == "src/auth/session.py"
    assert response.trace["nodes"]


def test_query_keeps_exact_anchor_card_before_lexical_candidates() -> None:
    graph = build_structural_graph(
        "repo:test",
        (
            SourceFile(
                path="src/auth/session.py",
                text='''def refresh_token():\n    """Refresh token before redirect handling."""\n    return True\n''',
            ),
            SourceFile(path="README.md", text="# Auth\n\nUse src/auth/session.py for refresh_token redirect behavior.\n"),
        ),
    )

    response = answer_structural_query(
        graph,
        HarnessQueryRequest(
            intent="file_context",
            user_goal="fix redirect token refresh",
            files=("src/auth/session.py",),
            max_cards=2,
            session_id="s1",
        ),
    )

    assert response.cards[0].type == "next_file"
    assert response.cards[0].evidence == ({"node_id": "file:repo:test:src/auth/session.py", "kind": "File"},)
    assert response.cards[1].type in {"doc_support", "symbol_context"}


def test_query_uses_vector_projection_when_lexical_has_no_grounded_candidate() -> None:
    graph = build_structural_graph(
        "repo:test",
        (
            SourceFile(
                path="src/auth/session.py",
                text='''def sign_in_user():\n    """Sign in user before redirect handling."""\n    return True\n''',
            ),
        ),
    )

    response = answer_structural_query(
        graph,
        HarnessQueryRequest(
            intent="edit_plan",
            user_goal="signin",
            max_cards=1,
            session_id="s1",
        ),
    )

    assert response.status == "partial_structural"
    assert response.cards[0].type == "symbol_context"
    assert response.cards[0].title == "Inspect sign_in_user"
    assert response.cards[0].evidence[1]["retrieval_source"] == "vector"
    assert response.cards[0].evidence[1]["embedding_method"] == "hash_token_char_cosine_v1"
    assert "candidate_discovery:vector_projection" in response.warnings
    assert "candidate_discovery:lexical_projection" not in response.warnings


def test_unanchored_edit_plan_aggregates_lexical_hits_to_source_files() -> None:
    graph = build_structural_graph(
        "repo:test",
        (
            SourceFile(
                path="src/agent_memory_orchestrator/infrastructure/sqlite/semantic_harness/graph_store.py",
                text="class SQLiteHarnessGraphStore:\n    def replace_edge(self):\n        pass\n    def edge_keys(self):\n        pass\n",
            ),
            SourceFile(
                path="src/agent_memory_orchestrator/domain/semantic_harness/snapshots.py",
                text="def graph_snapshot_identity():\n    return True\ndef graph_snapshot_id():\n    return True\n",
            ),
            SourceFile(
                path="src/agent_memory_orchestrator/domain/semantic_harness/doc_semantics/linking.py",
                text="def _append_edge():\n    pass\n",
            ),
        ),
    )

    response = answer_structural_query(
        graph,
        HarnessQueryRequest(
            intent="edit_plan",
            user_goal="semantic harness snapshot edge count higher than persisted SQLite edge count",
            errors=("semantic harness snapshot edge count higher than persisted SQLite edge count",),
            max_cards=3,
            session_id="s1",
        ),
    )

    assert response.status == "partial_structural"
    assert response.cards[0].type == "next_file"
    assert response.cards[0].title.endswith("graph_store.py")
    assert response.cards[0].evidence[1]["retrieval_source"] == "lexical_file_aggregate"
    assert response.cards[1].title.endswith("snapshots.py")
    assert "candidate_discovery:lexical_projection" in response.warnings
    assert "candidate_discovery:vector_projection" not in response.warnings


def test_query_reuses_projection_documents_between_lexical_and_vector(monkeypatch) -> None:
    graph = build_structural_graph(
        "repo:test",
        (SourceFile(path="src/main.py", text="def run():\n    return True\n"),),
    )
    calls = 0
    real_build = query_module.build_projection_documents

    def counting_build(*args, **kwargs):
        nonlocal calls
        calls += 1
        return real_build(*args, **kwargs)

    monkeypatch.setattr(query_module, "build_projection_documents", counting_build)

    response = answer_structural_query(
        graph,
        HarnessQueryRequest(
            intent="edit_plan",
            user_goal="billing invoice webhook",
            max_cards=2,
            session_id="s1",
        ),
    )

    assert response.status == "unavailable"
    assert response.cards == ()
    assert calls == 1
