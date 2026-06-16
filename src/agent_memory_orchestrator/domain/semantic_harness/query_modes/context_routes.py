from __future__ import annotations

from ..models import HarnessNode
from ..models import StructuralHarnessGraph
from .context_models import ActionRelevantLink
from .context_models import ContextAnswer
from .question_classifier import QuestionClassification


def answer_for_type(
    *,
    graph: StructuralHarnessGraph,
    anchor_nodes: tuple[HarnessNode, ...],
    classification: QuestionClassification,
    question_type: str,
    goal: str,
) -> tuple[list[ContextAnswer], list[ActionRelevantLink], list[str]]:
    if question_type == "semantic_role":
        return (_semantic_role_answers(anchor_nodes, classification), [], [])
    if question_type == "invariant":
        answers, invariants = _invariant_answers(anchor_nodes, classification)
        return answers, [], invariants
    if question_type == "validation":
        return ([], _links_for_edges(graph, anchor_nodes, kind="VALIDATED_BY", why="Validates the requested anchor behavior."), [])
    if question_type == "risk":
        links = _risk_links(graph, anchor_nodes)
        answers = _risk_answers(anchor_nodes, classification, has_links=bool(links), goal=goal)
        return answers, links, []
    if question_type == "usage":
        return ([], _usage_links(graph, anchor_nodes), [])
    return ([], [], [])


def dedupe_links(links: list[ActionRelevantLink]) -> list[ActionRelevantLink]:
    out: list[ActionRelevantLink] = []
    seen: set[tuple[str, str]] = set()
    for link in links:
        key = (link.kind, link.target_node_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(link)
    return out


def _semantic_role_answers(anchor_nodes: tuple[HarnessNode, ...], classification: QuestionClassification) -> list[ContextAnswer]:
    answers: list[ContextAnswer] = []
    for node in anchor_nodes:
        summary = str(node.summary or "").strip()
        if summary:
            text = summary
            confidence = 0.72
        else:
            text = f"{node.label} is a {node.kind} anchor in the structural graph. No reviewed semantic role is attached yet."
            confidence = 0.48
        answers.append(
            ContextAnswer(
                question=classification.question,
                question_type="semantic_role",
                answer=text,
                confidence=confidence,
                evidence=(_node_evidence(node),),
            )
        )
    return answers


def _invariant_answers(
    anchor_nodes: tuple[HarnessNode, ...],
    classification: QuestionClassification,
) -> tuple[list[ContextAnswer], list[str]]:
    answers: list[ContextAnswer] = []
    invariants: list[str] = []
    for node in anchor_nodes:
        invariant = str(node.metadata.get("invariant") or node.metadata.get("constraint") or "").strip()
        if invariant:
            invariants.append(invariant)
            answer = invariant
            confidence = 0.76
        else:
            answer = f"No reviewed invariant is attached to {node.label}; inspect code/tests before changing behavior."
            confidence = 0.42
        answers.append(
            ContextAnswer(
                question=classification.question,
                question_type="invariant",
                answer=answer,
                confidence=confidence,
                evidence=(_node_evidence(node),),
            )
        )
    return answers, invariants


def _risk_answers(
    anchor_nodes: tuple[HarnessNode, ...],
    classification: QuestionClassification,
    *,
    has_links: bool,
    goal: str,
) -> list[ContextAnswer]:
    qualifier = " Review the action-relevant links before editing." if has_links else " No action-relevant risk links were found."
    goal_part = f" for {goal}" if goal else ""
    return [
        ContextAnswer(
            question=classification.question,
            question_type="risk",
            answer=f"{node.label} has only structural risk evidence{goal_part}.{qualifier}",
            confidence=0.52 if has_links else 0.38,
            evidence=(_node_evidence(node),),
        )
        for node in anchor_nodes
    ]


def _risk_links(graph: StructuralHarnessGraph, anchor_nodes: tuple[HarnessNode, ...]) -> list[ActionRelevantLink]:
    links: list[ActionRelevantLink] = []
    for kind, why in (
        ("VALIDATED_BY", "Validation target to inspect before editing."),
        ("CO_CHANGED_WITH", "Historically co-changed; inspect only if relevant to the current task."),
    ):
        links.extend(_links_for_edges(graph, anchor_nodes, kind=kind, why=why))
    return links


def _usage_links(graph: StructuralHarnessGraph, anchor_nodes: tuple[HarnessNode, ...]) -> list[ActionRelevantLink]:
    links: list[ActionRelevantLink] = []
    for kind, why in (
        ("CALLS", "Direct usage edge requested by the question."),
        ("IMPORTS", "Direct import edge requested by the question."),
    ):
        links.extend(_links_for_edges(graph, anchor_nodes, kind=kind, why=why))
        links.extend(_incoming_links_for_edges(graph, anchor_nodes, kind=kind, why=why))
    return links


def _links_for_edges(
    graph: StructuralHarnessGraph,
    anchor_nodes: tuple[HarnessNode, ...],
    *,
    kind: str,
    why: str,
) -> list[ActionRelevantLink]:
    node_by_id = graph.node_by_id()
    links: list[ActionRelevantLink] = []
    for node in anchor_nodes:
        for edge in graph.outgoing(node.id, kind=kind):
            if target := node_by_id.get(edge.target_id):
                links.append(_link_from_node(kind=kind, target=target, why=why, confidence=edge.confidence))
    return links


def _incoming_links_for_edges(
    graph: StructuralHarnessGraph,
    anchor_nodes: tuple[HarnessNode, ...],
    *,
    kind: str,
    why: str,
) -> list[ActionRelevantLink]:
    node_by_id = graph.node_by_id()
    links: list[ActionRelevantLink] = []
    for node in anchor_nodes:
        for edge in graph.incoming(node.id, kind=kind):
            if source := node_by_id.get(edge.source_id):
                links.append(_link_from_node(kind=kind, target=source, why=why, confidence=edge.confidence))
    return links


def _link_from_node(*, kind: str, target: HarnessNode, why: str, confidence: float) -> ActionRelevantLink:
    return ActionRelevantLink(
        kind=kind,
        target_node_id=target.id,
        target_label=target.label,
        why=why,
        confidence=round(float(confidence or 0.0), 2),
    )


def _node_evidence(node: HarnessNode) -> dict[str, str]:
    return {"node_id": node.id, "kind": node.kind, "label": node.label}


__all__ = [
    "answer_for_type",
    "dedupe_links",
]
