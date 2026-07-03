from __future__ import annotations

from dataclasses import dataclass

from ..anchor_resolution import resolve_anchors
from ..models import StructuralHarnessGraph
from .context_models import ActionRelevantLink
from .context_models import ContextAnswer
from .context_models import ContextForAnchorResult
from .context_routes import answer_for_type
from .context_routes import dedupe_links
from .question_classifier import QuestionClassification
from .question_classifier import classify_context_questions


def answer_context_for_anchor(
    graph: StructuralHarnessGraph,
    *,
    goal: str = "",
    files: tuple[str, ...] = (),
    symbols: tuple[str, ...] = (),
    questions: tuple[str, ...] = (),
    max_results: int = 8,
) -> ContextForAnchorResult:
    anchors = resolve_anchors(graph, files=files, symbols=symbols)
    warnings: list[str] = []
    if not anchors.resolved:
        return ContextForAnchorResult(
            status="unavailable",
            answers=(),
            invariants=(),
            action_relevant_links=(),
            recommended_next_mode=None,
            question_classifications=(),
            warnings=("anchor_unresolved",),
        )
    if not tuple(question for question in questions if str(question).strip()):
        return ContextForAnchorResult(
            status="clarification_needed",
            answers=(),
            invariants=(),
            action_relevant_links=(),
            recommended_next_mode=None,
            question_classifications=(),
            warnings=("question_required",),
        )

    classifications = classify_context_questions(questions)
    node_by_id = graph.node_by_id()
    anchor_nodes = tuple(node for anchor in anchors.resolved if (node := node_by_id.get(anchor.node_id)) is not None)
    route_groups: list[_RouteOutput] = []
    links: list[ActionRelevantLink] = []
    invariants: list[str] = []
    recommended_mode = _recommended_mode(classifications)
    diagnostics: list[str] = []

    for classification in classifications:
        if classification.status == "clarification_needed":
            warnings.append(f"question_clarification_needed:{classification.question}")
            continue
        for question_type in classification.types:
            if question_type == "unknown":
                warnings.append(f"unknown_question_type:{classification.question}")
                continue
            route_answers, route_links, route_invariants = answer_for_type(
                graph=graph,
                anchor_nodes=anchor_nodes,
                classification=classification,
                question_type=question_type,
                goal=goal,
                diagnostics=diagnostics,
            )
            route_groups.append(_RouteOutput(question_type=question_type, answers=route_answers))
            links.extend(route_links)
            invariants.extend(route_invariants)

    route_groups, suppressed_missing_count = _suppress_parent_missing_answers(route_groups)
    if suppressed_missing_count:
        warnings.append(f"suppressed_parent_missing_answers:{suppressed_missing_count}")
    warnings.extend(diagnostics)
    answers = _merge_route_answers(route_groups, max_results=max_results)

    status = _status_for(answers=answers, links=links, warnings=warnings, recommended_mode=recommended_mode)
    if status == "partial_structural":
        warnings.append("structural_only:no_reviewed_semantic_context")
    return ContextForAnchorResult(
        status=status,
        answers=tuple(answers[:max_results]),
        invariants=tuple(dict.fromkeys(invariants)),
        action_relevant_links=tuple(dedupe_links(links)[:max_results]),
        recommended_next_mode=recommended_mode,
        question_classifications=classifications,
        warnings=tuple(dict.fromkeys(warnings)),
    )


@dataclass(slots=True)
class _RouteOutput:
    question_type: str
    answers: list[ContextAnswer]


def _recommended_mode(classifications: tuple[QuestionClassification, ...]) -> str | None:
    modes = tuple(
        classification.recommended_mode
        for classification in classifications
        if classification.status == "recommend_deeper_mode" and classification.recommended_mode
    )
    if not modes:
        return None
    return modes[0]


def _status_for(
    *,
    answers: list[ContextAnswer],
    links: list[ActionRelevantLink],
    warnings: list[str],
    recommended_mode: str | None,
) -> str:
    if recommended_mode and not answers and not links:
        return "partial_coverage"
    if not answers and not links:
        return "clarification_needed" if warnings else "unavailable"
    if any(answer.confidence >= 0.7 for answer in answers):
        return "ready"
    return "partial_structural"


def _merge_route_answers(route_groups: list[_RouteOutput], *, max_results: int) -> list[ContextAnswer]:
    if max_results <= 0:
        return []

    selected: list[ContextAnswer] = []
    seen: set[tuple[str, ...]] = set()
    primary_answers: list[ContextAnswer] = []

    for group in route_groups:
        primary = _primary_answer(group.answers)
        if primary is not None:
            primary_answers.append(primary)

    for answer in primary_answers:
        _append_unique_answer(selected, seen, answer, max_results=max_results)
        if len(selected) >= max_results:
            return selected

    primary_keys = {_answer_identity(answer) for answer in primary_answers}
    for group in route_groups:
        for answer in group.answers:
            if _answer_identity(answer) in primary_keys:
                continue
            _append_unique_answer(selected, seen, answer, max_results=max_results)
            if len(selected) >= max_results:
                return selected
    return selected


def _primary_answer(answers: list[ContextAnswer]) -> ContextAnswer | None:
    for answer in answers:
        if answer.review_status != "missing":
            return answer
    return answers[0] if answers else None


def _append_unique_answer(
    selected: list[ContextAnswer],
    seen: set[tuple[str, ...]],
    answer: ContextAnswer,
    *,
    max_results: int,
) -> None:
    if len(selected) >= max_results:
        return
    key = _answer_identity(answer)
    if key in seen:
        return
    seen.add(key)
    selected.append(answer)


def _answer_identity(answer: ContextAnswer) -> tuple[str, ...]:
    if answer.fact_id:
        return ("fact", answer.fact_id)
    return (
        "answer",
        answer.question,
        answer.question_type,
        answer.fact_type,
        answer.anchor_node_id,
        answer.answer,
    )


def _suppress_parent_missing_answers(route_groups: list[_RouteOutput]) -> tuple[list[_RouteOutput], int]:
    answers = [answer for group in route_groups for answer in group.answers]
    strong_symbol_keys = {
        (answer.question, answer.question_type)
        for answer in answers
        if answer.anchor_kind == "Symbol" and _is_strong_non_missing_answer(answer)
    }
    if not strong_symbol_keys:
        return route_groups, 0
    suppressed_keys: set[tuple[str, ...]] = set()
    suppressed = 0
    for answer in answers:
        if (
            answer.anchor_kind == "File"
            and answer.review_status == "missing"
            and (answer.question, answer.question_type) in strong_symbol_keys
        ):
            suppressed += 1
            suppressed_keys.add(_answer_identity(answer))
            continue
    filtered_groups = [
        _RouteOutput(
            question_type=group.question_type,
            answers=[answer for answer in group.answers if _answer_identity(answer) not in suppressed_keys],
        )
        for group in route_groups
    ]
    return filtered_groups, suppressed


def _is_strong_non_missing_answer(answer: ContextAnswer) -> bool:
    if answer.review_status == "missing":
        return False
    return answer.confidence >= 0.70 or answer.derivability in {
        "requires_git_history",
        "requires_agent_session_history",
        "requires_human_intent",
        "requires_runtime_observation",
    }


__all__ = [
    "ActionRelevantLink",
    "ContextAnswer",
    "ContextForAnchorResult",
    "answer_context_for_anchor",
]
