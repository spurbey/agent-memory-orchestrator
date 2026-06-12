from __future__ import annotations

from agent_memory_orchestrator.application.services.semantic_harness import RetrievalEvalCase
from agent_memory_orchestrator.application.services.semantic_harness import RetrievalHarnessEvalService
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryRequest


def test_retrieval_eval_distinguishes_exact_anchor_and_lexical_routes(tmp_path) -> None:
    _write_auth_fixture(tmp_path)

    report = RetrievalHarnessEvalService().evaluate_repo(
        tmp_path,
        repo_id="repo:test",
        cases=(
            RetrievalEvalCase(
                case_id="exact_file_anchor_no_lexical_when_budget_filled",
                request=HarnessQueryRequest(
                    intent="file_context",
                    user_goal="fix redirect token refresh",
                    files=("src/auth/session.py",),
                    max_cards=1,
                    session_id="eval",
                ),
                expected_status="partial_structural",
                expected_lexical_used=False,
                expected_first_card_type="next_file",
                expected_first_card_title="Inspect src/auth/session.py",
                forbidden_warning_terms=("candidate_discovery:lexical_projection",),
                required_next_action_targets=("src/auth/session.py",),
                min_card_count=1,
            ),
            RetrievalEvalCase(
                case_id="vague_goal_uses_lexical_projection",
                request=HarnessQueryRequest(
                    intent="edit_plan",
                    user_goal="fix redirect token refresh",
                    max_cards=2,
                    session_id="eval",
                ),
                expected_status="partial_structural",
                expected_lexical_used=True,
                expected_first_card_type="symbol_context",
                expected_first_card_title="Inspect refresh_token",
                required_warning_terms=("candidate_discovery:lexical_projection",),
                required_next_action_targets=("src/auth/session.py",),
                min_card_count=2,
            ),
        ),
    )

    assert report.passed is True
    assert report.contract_judgment["phase"] == "retrieval_mvp"
    assert report.contract_judgment["vector_used"] is False
    assert report.contract_judgment["candidates_must_ground_to_graph"] is True
    assert [case.lexical_used for case in report.cases] == [False, True]
    assert report.as_dict()["cases"][1]["graph_grounded_card_count"] == 2


def test_retrieval_eval_reports_unmatched_query_as_unavailable(tmp_path) -> None:
    _write_auth_fixture(tmp_path)

    report = RetrievalHarnessEvalService().evaluate_repo(
        tmp_path,
        repo_id="repo:test",
        cases=(
            RetrievalEvalCase(
                case_id="unmatched_query",
                request=HarnessQueryRequest(
                    intent="edit_plan",
                    user_goal="billing invoice webhook",
                    max_cards=2,
                    session_id="eval",
                ),
                expected_status="unavailable",
                expected_lexical_used=False,
                forbidden_warning_terms=("candidate_discovery:lexical_projection",),
            ),
        ),
    )

    assert report.passed is True
    assert report.cases[0].card_types == ()
    assert report.cases[0].graph_grounded_card_count == 0


def test_retrieval_eval_reports_wrong_route_expectation(tmp_path) -> None:
    _write_auth_fixture(tmp_path)

    report = RetrievalHarnessEvalService().evaluate_repo(
        tmp_path,
        repo_id="repo:test",
        cases=(
            RetrievalEvalCase(
                case_id="wrong_route",
                request=HarnessQueryRequest(
                    intent="edit_plan",
                    user_goal="fix redirect token refresh",
                    max_cards=1,
                    session_id="eval",
                ),
                expected_status="partial_structural",
                expected_lexical_used=False,
            ),
        ),
    )

    assert report.passed is False
    assert "lexical_used:True!=False" in report.cases[0].failure_reasons


def _write_auth_fixture(tmp_path) -> None:
    (tmp_path / "src" / "auth").mkdir(parents=True)
    (tmp_path / "src" / "auth" / "session.py").write_text(
        'def refresh_token():\n    """Refresh token before redirect handling."""\n    return True\n',
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(
        "# Auth\n\nUse src/auth/session.py for refresh_token redirect behavior.\n",
        encoding="utf-8",
    )
