from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SCORE_KEYS = (
    "semantic_correctness",
    "provenance_completeness",
    "retrieval_relevance",
    "merge_correctness",
    "no_hallucination",
    "answer_usefulness",
)


def judge_semantic_case(*, case: dict[str, Any], answer_payload: dict[str, Any], fixture: dict[str, Any] | None = None) -> dict[str, Any]:
    text = _answer_text(answer_payload)
    citations = _citations(answer_payload)
    blocking: list[str] = []

    missing_mentions = [term for term in case.get("must_mention", []) if str(term).lower() not in text.lower()]
    missing_cites = [term for term in case.get("must_cite", []) if not _citation_present(citations, str(term))]
    forbidden_claims = [term for term in case.get("must_not_claim", []) if str(term).lower() in text.lower()]

    if missing_mentions:
        blocking.append(f"missing_required_mentions:{','.join(map(str, missing_mentions))}")
    if missing_cites:
        blocking.append(f"missing_required_citations:{','.join(map(str, missing_cites))}")
    if forbidden_claims:
        blocking.append(f"forbidden_claims_present:{','.join(map(str, forbidden_claims))}")

    invariant_failures = _case_invariant_failures(case, fixture or {})
    blocking.extend(invariant_failures)

    embedding_status = ((fixture or {}).get("embedding_coverage") or {}).get("status", "")
    scores = {key: 1.0 for key in SCORE_KEYS}
    if missing_mentions:
        scores["semantic_correctness"] = 0.0
        scores["answer_usefulness"] = 0.25
    if missing_cites:
        scores["provenance_completeness"] = 0.0
    if forbidden_claims:
        scores["no_hallucination"] = 0.0
    if invariant_failures:
        scores["merge_correctness"] = 0.0
        scores["semantic_correctness"] = min(scores["semantic_correctness"], 0.5)
    if embedding_status == "partial":
        scores["retrieval_relevance"] = 0.5

    passed = not blocking
    return {
        "case_id": case.get("case_id", ""),
        "passed": passed,
        "scores": scores,
        "explanation": _explanation(case, missing_mentions, missing_cites, forbidden_claims, invariant_failures, embedding_status),
        "blocking_failures": blocking,
        "judge_mode": "fixture_semantic_rubric_v1",
    }


def run_semantic_eval_fixture(*, fixture_path: Path, case_set: str = "baseline") -> dict[str, Any]:
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    cases = _default_cases(case_set)
    judgments = []
    for case in cases:
        answer_payload = _fixture_answer_payload(fixture, case)
        judgments.append(judge_semantic_case(case=case, answer_payload=answer_payload, fixture=fixture))
    passed = all(item["passed"] for item in judgments)
    return {
        "ok": True,
        "status": "passed" if passed else "failed",
        "case_set": case_set,
        "fixture_path": str(fixture_path),
        "judgments": judgments,
        "metrics": {
            "case_count": len(judgments),
            "passed_count": sum(1 for item in judgments if item["passed"]),
            "judge_blocking_failure_count": sum(len(item["blocking_failures"]) for item in judgments),
        },
    }


def _default_cases(case_set: str) -> list[dict[str, Any]]:
    if case_set != "baseline":
        return []
    return [
        {
            "case_id": "fixture-has-traceable-v2-job",
            "query": "does this fixture contain traceable V2 job output?",
            "must_mention": ["job", "stage"],
            "must_cite": ["stage"],
            "must_not_claim": ["unsupported final decision"],
            "expected_status": "active",
            "requires": ["job_present", "stage_rows_present"],
        },
        {
            "case_id": "central-merge-plan-is-inspectable",
            "query": "did session graph output produce an inspectable central merge plan?",
            "must_mention": ["central", "merge", "repo"],
            "must_cite": ["central_version_merge"],
            "must_not_claim": ["canonical graph applied"],
            "requires": ["central_plan_present", "repo_id_present", "graph_commit_preview_present"],
        },
        {
            "case_id": "central-plan-keeps-decisions-dry-run",
            "query": "did the merge plan avoid exact decision canonicalization?",
            "must_mention": ["decision", "dry-run"],
            "must_cite": ["central_version_merge"],
            "must_not_claim": ["decision atom applied"],
            "requires": ["no_exact_decision_atoms"],
        },
        {
            "case_id": "central-plan-version-provenance-ready",
            "query": "do central versions have session-node provenance anchors?",
            "must_mention": ["version", "source"],
            "must_cite": ["central_version_merge"],
            "must_not_claim": ["answer-grade without provenance"],
            "requires": ["version_sources_present"],
        },
        {
            "case_id": "embedding-coverage-is-truthful",
            "query": "does the fixture report vector coverage honestly?",
            "must_mention": ["embedding", "coverage"],
            "must_cite": ["retrieval"],
            "must_not_claim": ["vector ready without coverage"],
            "requires": ["embedding_coverage_consistent"],
        }
    ]


def _fixture_answer_payload(fixture: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    stages = fixture.get("stages") if isinstance(fixture.get("stages"), list) else []
    context = fixture.get("semantic_context") if isinstance(fixture.get("semantic_context"), dict) else {}
    central = context.get("central_version_merge") if isinstance(context.get("central_version_merge"), dict) else {}
    retrieval = context.get("retrieval") if isinstance(context.get("retrieval"), dict) else {}
    case_id = str(case.get("case_id") or "")
    if case_id == "central-merge-plan-is-inspectable":
        return {
            "answer": (
                f"Central merge dry-run plan {central.get('plan_id', '')} is inspectable for repo "
                f"{central.get('repo_id', '')}; it is not an applied canonical graph commit."
            ),
            "citations": [{"type": "central_version_merge", "id": central.get("plan_id", "")}],
        }
    if case_id == "central-plan-keeps-decisions-dry-run":
        return {
            "answer": f"Decision merge remains dry-run; atom kinds are {central.get('atom_kinds', [])}.",
            "citations": [{"type": "central_version_merge", "id": central.get("plan_id", "")}],
        }
    if case_id == "central-plan-version-provenance-ready":
        return {
            "answer": "Every central version preview should keep source session-node anchors before any answer-grade promotion.",
            "citations": [{"type": "central_version_merge", "id": central.get("plan_id", "")}],
        }
    if case_id == "embedding-coverage-is-truthful":
        coverage = retrieval.get("embedding_coverage") if isinstance(retrieval.get("embedding_coverage"), dict) else {}
        return {
            "answer": (
                f"Embedding coverage is reported as {coverage.get('status', 'missing')} with "
                f"{coverage.get('embedded_docs', 0)} embedded docs out of {coverage.get('total_docs', 0)}."
            ),
            "citations": [{"type": "retrieval", "id": "embedding_coverage"}],
        }
    return {
        "answer": f"Fixture contains job {((fixture.get('job') or {}).get('job_id') or '')} with {len(stages)} stage rows.",
        "citations": [{"type": "stage", "id": stage.get("stage", "")} for stage in stages[:5] if isinstance(stage, dict)],
    }


def _case_invariant_failures(case: dict[str, Any], fixture: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    context = fixture.get("semantic_context") if isinstance(fixture.get("semantic_context"), dict) else {}
    central = context.get("central_version_merge") if isinstance(context.get("central_version_merge"), dict) else {}
    retrieval = context.get("retrieval") if isinstance(context.get("retrieval"), dict) else {}
    requirements = [str(item) for item in case.get("requires", []) if str(item)]
    for requirement in requirements:
        if requirement == "job_present" and not fixture.get("job"):
            failures.append("job_missing")
        elif requirement == "stage_rows_present" and not fixture.get("stages"):
            failures.append("stage_rows_missing")
        elif requirement == "central_plan_present" and not central.get("available"):
            failures.append("central_merge_plan_missing")
        elif requirement == "repo_id_present" and not str(central.get("repo_id") or "").startswith("repo:"):
            failures.append("repo_id_missing_or_invalid")
        elif requirement == "graph_commit_preview_present" and not _has_graph_commit_preview(central):
            failures.append("graph_commit_preview_missing")
        elif requirement == "no_exact_decision_atoms" and "decision" in set(central.get("atom_kinds") or []):
            failures.append("decision_atom_created_too_early")
        elif requirement == "version_sources_present" and central.get("new_version_count", 0) and central.get("version_source_complete") is not True:
            failures.append("central_version_source_nodes_missing")
        elif requirement == "embedding_coverage_consistent" and not _embedding_coverage_consistent(retrieval):
            failures.append("embedding_coverage_inconsistent")
    return failures


def _has_graph_commit_preview(central: dict[str, Any]) -> bool:
    preview = central.get("graph_commit_preview") if isinstance(central.get("graph_commit_preview"), dict) else {}
    return str(preview.get("graph_commit_id") or "").startswith("v2gcommit:")


def _embedding_coverage_consistent(retrieval: dict[str, Any]) -> bool:
    coverage = retrieval.get("embedding_coverage") if isinstance(retrieval.get("embedding_coverage"), dict) else {}
    total = int(coverage.get("total_docs") or 0)
    embedded = int(coverage.get("embedded_docs") or 0)
    status = str(coverage.get("status") or "")
    if total < 0 or embedded < 0 or embedded > total:
        return False
    if total == 0:
        return status in {"missing", ""}
    if embedded >= total:
        return status == "ready"
    if embedded > 0:
        return status == "partial"
    return status == "missing"


def _answer_text(answer_payload: dict[str, Any]) -> str:
    for key in ("answer", "text", "summary"):
        value = answer_payload.get(key)
        if isinstance(value, str):
            return value
    return json.dumps(answer_payload, sort_keys=True)


def _citations(answer_payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = answer_payload.get("citations") or answer_payload.get("trace") or []
    return value if isinstance(value, list) else []


def _citation_present(citations: list[dict[str, Any]], needle: str) -> bool:
    lowered = needle.lower()
    for citation in citations:
        if lowered in json.dumps(citation, sort_keys=True).lower():
            return True
    return False


def _explanation(
    case: dict[str, Any],
    missing_mentions: list[Any],
    missing_cites: list[Any],
    forbidden_claims: list[Any],
    invariant_failures: list[str],
    embedding_status: str,
) -> str:
    if not missing_mentions and not missing_cites and not forbidden_claims and not invariant_failures:
        suffix = " Vector-dependent scores are partial." if embedding_status == "partial" else ""
        return f"Case {case.get('case_id', '')} passed fixture semantic rubric.{suffix}"
    return "Fixture semantic rubric failed: " + "; ".join(
        part
        for part in [
            f"missing mentions {missing_mentions}" if missing_mentions else "",
            f"missing citations {missing_cites}" if missing_cites else "",
            f"forbidden claims {forbidden_claims}" if forbidden_claims else "",
            f"invariant failures {invariant_failures}" if invariant_failures else "",
        ]
        if part
    )
