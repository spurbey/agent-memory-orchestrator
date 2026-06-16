from __future__ import annotations

from ..models import HarnessNode
from ..models import StructuralHarnessGraph
from .context_models import ActionRelevantLink
from .context_models import ContextAnswer
from .question_classifier import QuestionClassification
from .semantic_facts import SemanticFact
from .semantic_facts import best_fact_for_node
from .semantic_facts import facts_need_non_derivable


def answer_for_type(
    *,
    graph: StructuralHarnessGraph,
    anchor_nodes: tuple[HarnessNode, ...],
    classification: QuestionClassification,
    question_type: str,
    goal: str,
) -> tuple[list[ContextAnswer], list[ActionRelevantLink], list[str]]:
    if question_type == "semantic_role":
        return (_fact_answers(anchor_nodes, classification, question_type=question_type, fact_types=("semantic_role",)), [], [])
    if question_type == "invariant":
        answers, invariants = _invariant_answers(anchor_nodes, classification, question_type=question_type)
        return answers, [], invariants
    if question_type == "validation":
        return ([], _links_for_edges(graph, anchor_nodes, kind="VALIDATED_BY", why="Validates the requested anchor behavior."), [])
    if question_type == "risk":
        links = _risk_links(graph, anchor_nodes)
        answers = _risk_answers(anchor_nodes, classification, has_links=bool(links), goal=goal)
        return answers, links, []
    if question_type == "history":
        return (
            _fact_answers(
                anchor_nodes,
                classification,
                question_type=question_type,
                fact_types=("implementation_rationale", "historical_change"),
            ),
            [],
            [],
        )
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


def _invariant_answers(
    anchor_nodes: tuple[HarnessNode, ...],
    classification: QuestionClassification,
    *,
    question_type: str,
) -> tuple[list[ContextAnswer], list[str]]:
    answers: list[ContextAnswer] = []
    invariants: list[str] = []
    for node in anchor_nodes:
        fact = best_fact_for_node(
            node,
            fact_types=("invariant_or_contract", "data_model_or_storage"),
            prefer_non_derivable=facts_need_non_derivable(question_type),
        )
        if fact:
            invariants.append(fact.text)
            answers.append(_answer_from_fact(classification=classification, question_type="invariant", fact=fact, fallback_node=node))
            continue
        answers.append(_missing_fact_answer(classification=classification, node=node, question_type="invariant", fact_type="invariant_or_contract"))
    return answers, invariants


def _fact_answers(
    anchor_nodes: tuple[HarnessNode, ...],
    classification: QuestionClassification,
    *,
    question_type: str,
    fact_types: tuple[str, ...],
) -> list[ContextAnswer]:
    answers: list[ContextAnswer] = []
    prefer_non_derivable = facts_need_non_derivable(question_type)
    for node in anchor_nodes:
        fact = best_fact_for_node(node, fact_types=fact_types, prefer_non_derivable=prefer_non_derivable)
        if fact:
            answers.append(_answer_from_fact(classification=classification, question_type=question_type, fact=fact, fallback_node=node))
            continue
        answers.append(_missing_fact_answer(classification=classification, node=node, question_type=question_type, fact_type=fact_types[0]))
    return answers


def _answer_from_fact(
    *,
    classification: QuestionClassification,
    question_type: str,
    fact: SemanticFact,
    fallback_node: HarnessNode,
) -> ContextAnswer:
    evidence = fact.source_refs or (_node_evidence(fallback_node),)
    return ContextAnswer(
        question=classification.question,
        question_type=question_type,
        answer=fact.text,
        confidence=fact.confidence,
        evidence=evidence,
        fact_type=fact.fact_type,
        derivability=fact.derivability,
        review_status=fact.review_status,
        discovery_cost=fact.discovery_cost,
        source_kind=fact.source_kind,
        fact_scope=fact.fact_scope,
        verification_status=fact.verification_status,
        trust_tier=fact.trust_tier,
    )


def _missing_fact_answer(
    *,
    classification: QuestionClassification,
    node: HarnessNode,
    question_type: str,
    fact_type: str,
) -> ContextAnswer:
    readable = _readable_fact_type(fact_type)
    return ContextAnswer(
        question=classification.question,
        question_type=question_type,
        answer=f"No reviewed {readable} fact is attached to {node.label}; inspect code/tests/history before changing behavior.",
        confidence=0.42,
        evidence=(_node_evidence(node),),
        fact_type=fact_type,
        review_status="missing",
        derivability="unknown",
        discovery_cost="unknown",
        source_kind="",
        fact_scope="",
        verification_status="unknown",
        trust_tier=99,
    )


def _risk_answers(
    anchor_nodes: tuple[HarnessNode, ...],
    classification: QuestionClassification,
    *,
    has_links: bool,
    goal: str,
) -> list[ContextAnswer]:
    fact_answers = _fact_answers(
        anchor_nodes,
        classification,
        question_type="risk",
        fact_types=("risk_or_impact", "failure_mode", "implementation_rationale"),
    )
    if any(answer.review_status != "missing" for answer in fact_answers):
        return fact_answers
    qualifier = " Review the action-relevant links before editing." if has_links else " No action-relevant risk links were found."
    goal_part = f" for {goal}" if goal else ""
    return [
        ContextAnswer(
            question=classification.question,
            question_type="risk",
            answer=f"{node.label} has only structural risk evidence{goal_part}.{qualifier}",
            confidence=0.52 if has_links else 0.38,
            evidence=(_node_evidence(node),),
            fact_type="risk_or_impact",
            derivability="unknown",
            review_status="missing",
            discovery_cost="unknown",
            source_kind="",
            fact_scope="",
            verification_status="unknown",
            trust_tier=99,
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


def _readable_fact_type(fact_type: str) -> str:
    return fact_type.replace("_", " ")


__all__ = [
    "answer_for_type",
    "dedupe_links",
]
