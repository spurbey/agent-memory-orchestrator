from __future__ import annotations

from agent_memory_orchestrator.application.services.semantic_harness import StructuralEvalCase
from agent_memory_orchestrator.application.services.semantic_harness import StructuralHarnessEvalService
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryRequest


def test_structural_eval_reports_passed_contract_and_query_shape(tmp_path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "pkg" / "a.py").write_text(
        "from .b import helper\n\n\ndef run():\n    return helper()\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "pkg" / "b.py").write_text(
        "def helper():\n    return True\n",
        encoding="utf-8",
    )

    report = StructuralHarnessEvalService().evaluate_repo(
        tmp_path,
        repo_id="repo:test",
        cases=(
            StructuralEvalCase(
                case_id="imported_dependency_context",
                request=HarnessQueryRequest(
                    intent="file_context",
                    user_goal="inspect helper dependency before editing",
                    files=("src/pkg/a.py",),
                    max_cards=3,
                ),
                expected_status="partial_structural",
                required_card_types=("next_file", "dependency"),
                required_title_terms=("Check imported file src/pkg/b.py",),
                forbidden_card_types=("why_changed",),
            ),
        ),
    )

    assert report.passed is True
    assert report.contract_judgment["raw_ast_flood"] is False
    assert report.contract_judgment["structural_versions_present"] is True
    assert report.contract_judgment["qwen_used"] is False
    assert report.contract_judgment["vector_used"] is False
    assert report.cases[0].card_types == ("next_file", "dependency", "symbol_context")
    assert report.as_dict()["cases"][0]["passed"] is True


def test_structural_eval_reports_status_mismatch_failure(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text(
        "def login():\n    return True\n",
        encoding="utf-8",
    )

    report = StructuralHarnessEvalService().evaluate_repo(
        tmp_path,
        repo_id="repo:test",
        cases=(
            StructuralEvalCase(
                case_id="not_ready_in_structural_phase",
                request=HarnessQueryRequest(
                    intent="file_context",
                    user_goal="inspect login behavior",
                    files=("src/auth.py",),
                ),
                expected_status="ready",
            ),
        ),
    )

    assert report.passed is False
    assert report.cases[0].passed is False
    assert report.cases[0].actual_status == "partial_structural"
    assert "status:partial_structural!=ready" in report.cases[0].failure_reasons


def test_structural_eval_reports_missing_required_card_failure(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("value = 1\n", encoding="utf-8")

    report = StructuralHarnessEvalService().evaluate_repo(
        tmp_path,
        repo_id="repo:test",
        cases=(
            StructuralEvalCase(
                case_id="missing_dependency_card",
                request=HarnessQueryRequest(
                    intent="file_context",
                    user_goal="inspect main",
                    files=("src/main.py",),
                ),
                expected_status="partial_structural",
                required_card_types=("dependency",),
            ),
        ),
    )

    assert report.passed is False
    assert "missing_card_type:dependency" in report.cases[0].failure_reasons
