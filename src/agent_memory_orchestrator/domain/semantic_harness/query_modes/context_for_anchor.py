from __future__ import annotations

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
    answers: list[ContextAnswer] = []
    links: list[ActionRelevantLink] = []
    invariants: list[str] = []
    recommended_mode = _recommended_mode(classifications)

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
            )
            answers.extend(route_answers)
            links.extend(route_links)
            invariants.extend(route_invariants)

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


__all__ = [
    "ActionRelevantLink",
    "ContextAnswer",
    "ContextForAnchorResult",
    "answer_context_for_anchor",
]
