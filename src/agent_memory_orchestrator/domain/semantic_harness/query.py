from __future__ import annotations

from .anchor_resolution import resolve_anchors
from .identity import harness_card_id
from .models import HarnessCard
from .models import HarnessNextAction
from .models import HarnessQueryRequest
from .models import HarnessQueryResponse
from .models import ResolvedAnchor
from .models import StructuralHarnessGraph
from .relations import HistoricalRelationCandidate
from .relations import HistoricalRelationPolicy
from .relations import historical_relation_candidates


SUPPORTED_INTENTS = {"edit_plan", "file_context"}


def answer_structural_query(
    graph: StructuralHarnessGraph,
    request: HarnessQueryRequest,
    *,
    historical_relation_policy: HistoricalRelationPolicy = HistoricalRelationPolicy(),
) -> HarnessQueryResponse:
    intent_requested = str(request.intent or "").strip() or "file_context"
    intent_used = intent_requested if intent_requested in SUPPORTED_INTENTS else "file_context"
    warnings: list[str] = []
    if intent_requested != intent_used:
        warnings.append(f"unsupported_intent:{intent_requested}")

    anchors = resolve_anchors(graph, files=request.files, symbols=request.symbols)
    status = _status_for(anchors.resolved, anchors.unresolved)
    node_by_id = graph.node_by_id()
    cards: list[HarnessCard] = []
    seen_cards = set(request.already_seen_card_ids)
    seen_node_ids = set(request.already_seen_node_ids)
    selected_node_ids: set[str] = set()
    max_cards = max(1, int(request.max_cards or 5))
    for anchor in anchors.resolved:
        node = node_by_id.get(anchor.node_id)
        if node is None or node.id in seen_node_ids:
            continue
        card = _card_for_anchor(graph=graph, request=request, intent=intent_used, anchor=anchor)
        if card.card_id in seen_cards or node.id in selected_node_ids:
            continue
        cards.append(card)
        selected_node_ids.add(node.id)
        if len(cards) >= max_cards:
            break
    for anchor in anchors.resolved:
        if len(cards) >= max_cards:
            break
        for dependency_card in _dependency_cards_for_anchor(
            graph=graph,
            request=request,
            intent=intent_used,
            anchor_node_id=anchor.node_id,
            seen_cards=seen_cards,
            seen_node_ids=seen_node_ids | selected_node_ids,
        ):
            cards.append(dependency_card)
            selected_node_ids.update(evidence["node_id"] for evidence in dependency_card.evidence if "node_id" in evidence)
            if len(cards) >= max_cards:
                break
    for anchor in anchors.resolved:
        if len(cards) >= max_cards:
            break
        for historical_card in _historical_relation_cards_for_anchor(
            graph=graph,
            request=request,
            intent=intent_used,
            anchor_node_id=anchor.node_id,
            seen_cards=seen_cards,
            seen_node_ids=seen_node_ids | selected_node_ids,
            seen_relation_ids=set(request.already_seen_relation_ids),
            policy=historical_relation_policy,
        ):
            cards.append(historical_card)
            selected_node_ids.update(
                evidence["node_id"]
                for evidence in historical_card.evidence
                if evidence.get("kind") in {"Symbol", "CodeRegion", "File"}
            )
            if len(cards) >= max_cards:
                break
    for anchor in anchors.resolved:
        if len(cards) >= max_cards:
            break
        node = node_by_id.get(anchor.node_id)
        if node is None or node.kind != "File":
            continue
        for child_card in _child_cards_for_file_anchor(
            graph=graph,
            request=request,
            intent=intent_used,
            file_node_id=node.id,
            seen_cards=seen_cards,
            seen_node_ids=seen_node_ids | selected_node_ids,
        ):
            cards.append(child_card)
            selected_node_ids.update(evidence["node_id"] for evidence in child_card.evidence if evidence.get("kind") in {"Symbol", "CodeRegion"})
            if len(cards) >= max_cards:
                break

    actions = tuple(_next_action_for_card(card) for card in cards)
    if status == "partial_structural":
        warnings.append("structural_only:no_work_history_or_semantic_reasoning_attached")
    if anchors.unresolved:
        warnings.append("unresolved_anchors:" + ",".join(anchors.unresolved))
    trace = _trace_for(graph=graph, anchors=anchors.resolved, cards=tuple(cards))

    return HarnessQueryResponse(
        status=status,
        intent_requested=intent_requested,
        intent_used=intent_used,
        intent_correction=None,
        cards=tuple(cards),
        next_actions=actions,
        trace=trace,
        warnings=tuple(warnings),
    )


def _historical_relation_cards_for_anchor(
    *,
    graph: StructuralHarnessGraph,
    request: HarnessQueryRequest,
    intent: str,
    anchor_node_id: str,
    seen_cards: set[str],
    seen_node_ids: set[str],
    seen_relation_ids: set[str],
    policy: HistoricalRelationPolicy,
) -> tuple[HarnessCard, ...]:
    node_by_id = graph.node_by_id()
    anchor = node_by_id.get(anchor_node_id)
    if anchor is None:
        return ()
    out: list[HarnessCard] = []
    for candidate in historical_relation_candidates(graph, anchor_node_id=anchor_node_id, policy=policy):
        relation_key = f"{candidate.edge.source_id}|{candidate.edge.kind}|{candidate.edge.target_id}"
        if relation_key in seen_relation_ids or candidate.related_id in seen_node_ids:
            continue
        card_id = harness_card_id(
            graph.repo_id,
            request.session_id,
            intent,
            (candidate.edge.source_id, candidate.edge.kind, candidate.edge.target_id, "historical_relation"),
        )
        if card_id in seen_cards:
            continue
        out.append(_historical_relation_card(anchor=anchor, candidate=candidate, card_id=card_id, policy=policy))
    return tuple(out)


def _historical_relation_card(
    *,
    anchor: object,
    candidate: HistoricalRelationCandidate,
    card_id: str,
    policy: HistoricalRelationPolicy,
) -> HarnessCard:
    edge = candidate.edge
    related = candidate.related_node
    metadata = edge.metadata
    cochange_count = int(metadata.get("cochange_count") or 0)
    stored_strength = float(metadata.get("stored_strength") or edge.weight or 0.0)
    jaccard = float(metadata.get("jaccard") or 0.0)
    occurrences = _historical_occurrence_evidence(candidate)
    evidence: tuple[dict[str, str], ...] = (
        {"node_id": str(getattr(anchor, "id", "")), "kind": str(getattr(anchor, "kind", ""))},
        {"node_id": related.id, "kind": related.kind},
        {
            "source_id": edge.source_id,
            "target_id": edge.target_id,
            "kind": edge.kind,
            "stored_strength": f"{stored_strength:.2f}",
            "cochange_count": str(cochange_count),
            "min_cochange_count": str(policy.min_cochange_count),
            "jaccard": f"{jaccard:.4f}",
        },
        *occurrences,
    )
    related_target = related.metadata.get("path") or related.label
    return HarnessCard(
        card_id=card_id,
        type="historical_relation",
        title=f"Inspect historically co-changed {related.label}",
        why=(
            f"{getattr(anchor, 'label', '')} and {related.label} co-changed {cochange_count} times "
            f"(strength {stored_strength:.2f}, Jaccard {jaccard:.2f})."
        ),
        evidence=evidence,
        risk="Historical structural signal only; semantic reasons may still be pending.",
        confidence=round(min(0.78, stored_strength + 0.18), 2),
        next_action=f"Inspect {related_target} before patching {getattr(anchor, 'label', '')}.",
    )


def _historical_occurrence_evidence(candidate: HistoricalRelationCandidate) -> tuple[dict[str, str], ...]:
    out: list[dict[str, str]] = []
    for occurrence in candidate.occurrence_nodes:
        metadata = occurrence.metadata
        out.append(
            {
                "node_id": occurrence.id,
                "kind": occurrence.kind,
                "commit_id": str(metadata.get("commit_id") or ""),
                "work_window_id": str(metadata.get("work_window_id") or ""),
                "reason_status": str(metadata.get("reason_status") or "semantic_pending"),
            }
        )
    return tuple(out)


def _child_cards_for_file_anchor(
    *,
    graph: StructuralHarnessGraph,
    request: HarnessQueryRequest,
    intent: str,
    file_node_id: str,
    seen_cards: set[str],
    seen_node_ids: set[str],
) -> tuple[HarnessCard, ...]:
    if not file_node_id:
        return ()
    node_by_id = graph.node_by_id()
    out: list[HarnessCard] = []
    edges = sorted(
        (edge for edge in graph.outgoing(file_node_id) if edge.kind in {"DEFINES", "CONTAINS"}),
        key=lambda edge: (
            _child_kind_rank(node_by_id.get(edge.target_id).kind if node_by_id.get(edge.target_id) else ""),
            int((node_by_id.get(edge.target_id).metadata if node_by_id.get(edge.target_id) else {}).get("line_start") or 0),
            edge.target_id,
        ),
    )
    for edge in edges:
        child = node_by_id.get(edge.target_id)
        if child is None or child.kind not in {"Symbol", "CodeRegion"} or child.id in seen_node_ids:
            continue
        card_id = harness_card_id(graph.repo_id, request.session_id, intent, (file_node_id, child.id))
        if card_id in seen_cards:
            continue
        out.append(
            HarnessCard(
                card_id=card_id,
                type="symbol_context" if child.kind == "Symbol" else "dependency",
                title=f"Inspect {child.label}",
                why=f"{child.label} is defined inside the anchored file and is available as structural context.",
                evidence=(
                    {"node_id": file_node_id, "kind": "File"},
                    {"node_id": child.id, "kind": child.kind},
                    {"source_id": file_node_id, "target_id": child.id, "kind": edge.kind},
                ),
                risk="Structural-only card; no work-history reason is attached in Phase 1.",
                confidence=0.72,
                next_action=f"Inspect {child.metadata.get('path') or child.label} around {child.label}.",
            )
        )
    return tuple(out)


def _dependency_cards_for_anchor(
    *,
    graph: StructuralHarnessGraph,
    request: HarnessQueryRequest,
    intent: str,
    anchor_node_id: str,
    seen_cards: set[str],
    seen_node_ids: set[str],
) -> tuple[HarnessCard, ...]:
    node_by_id = graph.node_by_id()
    anchor = node_by_id.get(anchor_node_id)
    if anchor is None:
        return ()
    edge_kinds = {"IMPORTS"} if anchor.kind == "File" else {"CALLS"} if anchor.kind == "Symbol" else set()
    if not edge_kinds:
        return ()
    out: list[HarnessCard] = []
    edges = sorted(
        (edge for edge in graph.outgoing(anchor_node_id) if edge.kind in edge_kinds),
        key=lambda edge: (
            edge.kind,
            node_by_id.get(edge.target_id).label if node_by_id.get(edge.target_id) else edge.target_id,
        ),
    )
    for edge in edges:
        target = node_by_id.get(edge.target_id)
        if target is None or target.id in seen_node_ids:
            continue
        card_id = harness_card_id(graph.repo_id, request.session_id, intent, (edge.source_id, edge.kind, edge.target_id))
        if card_id in seen_cards:
            continue
        title, why, next_action = _dependency_card_text(anchor=anchor, target=target, edge_kind=edge.kind)
        out.append(
            HarnessCard(
                card_id=card_id,
                type="dependency",
                title=title,
                why=why,
                evidence=(
                    {"node_id": anchor.id, "kind": anchor.kind},
                    {"node_id": target.id, "kind": target.kind},
                    {"source_id": edge.source_id, "target_id": edge.target_id, "kind": edge.kind},
                ),
                risk="Structural-only dependency; verify runtime behavior before broad edits.",
                confidence=0.68,
                next_action=next_action,
            )
        )
    return tuple(out)


def _dependency_card_text(*, anchor: object, target: object, edge_kind: str) -> tuple[str, str, str]:
    anchor_label = str(getattr(anchor, "label", ""))
    target_label = str(getattr(target, "label", ""))
    target_path = str(getattr(target, "metadata", {}).get("path") or target_label)
    if edge_kind == "IMPORTS":
        return (
            f"Check imported file {target_label}",
            f"{anchor_label} imports {target_label} through a local structural import edge.",
            f"Inspect {target_path} if the edit depends on imported behavior.",
        )
    return (
        f"Check called symbol {target_label}",
        f"{anchor_label} calls {target_label} through a conservative same-file call edge.",
        f"Inspect {target_path} around {target_label} if changing the caller.",
    )


def _child_kind_rank(kind: str) -> int:
    return {"Symbol": 0, "CodeRegion": 1}.get(kind, 9)


def _status_for(resolved: tuple[ResolvedAnchor, ...], unresolved: tuple[str, ...]) -> str:
    if resolved and unresolved:
        return "partial_coverage"
    if resolved:
        return "partial_structural"
    return "unavailable"


def _card_for_anchor(
    *,
    graph: StructuralHarnessGraph,
    request: HarnessQueryRequest,
    intent: str,
    anchor: ResolvedAnchor,
) -> HarnessCard:
    node = graph.node_by_id()[anchor.node_id]
    card_type = "next_file" if node.kind == "File" else "symbol_context"
    title = f"Inspect {node.label}"
    why = _why_for(node_kind=node.kind, node_label=node.label, reason=anchor.reason)
    evidence = ({"node_id": node.id, "kind": node.kind},)
    card_id = harness_card_id(graph.repo_id, request.session_id, intent, (node.id,))
    return HarnessCard(
        card_id=card_id,
        type=card_type,
        title=title,
        why=why,
        evidence=evidence,
        risk="Structural-only card; verify behavior in code before editing.",
        confidence=round(min(0.82, anchor.confidence * 0.8), 2),
        next_action=f"Open {node.metadata.get('path') or node.label} and inspect before editing.",
    )


def _why_for(*, node_kind: str, node_label: str, reason: str) -> str:
    if node_kind == "Symbol":
        return f"{node_label} resolved from an exact structural symbol anchor ({reason})."
    return f"{node_label} resolved from an exact structural file anchor ({reason})."


def _next_action_for_card(card: HarnessCard) -> HarnessNextAction:
    if card.type == "historical_relation":
        target = card.evidence[1].get("node_id", card.title) if len(card.evidence) > 1 else card.title
        return HarnessNextAction(
            action_type="inspect_file",
            target=target,
            reason=card.why,
            priority="recommended",
        )
    return HarnessNextAction(
        action_type="inspect_file",
        target=card.next_action.removeprefix("Open ").removesuffix(" and inspect before editing."),
        reason=card.why,
        priority="recommended",
    )


def _trace_for(
    *,
    graph: StructuralHarnessGraph,
    anchors: tuple[ResolvedAnchor, ...],
    cards: tuple[HarnessCard, ...],
) -> dict[str, list[object]]:
    node_ids: list[str] = []
    edge_refs: list[dict[str, str]] = []
    occurrence_ids: list[str] = []
    for anchor in anchors:
        _append_unique(node_ids, anchor.node_id)
    for card in cards:
        for evidence in card.evidence:
            if node_id := evidence.get("node_id"):
                if evidence.get("kind") == "RelationOccurrence":
                    _append_unique(occurrence_ids, node_id)
                else:
                    _append_unique(node_ids, node_id)
            if source_id := evidence.get("source_id"):
                target_id = evidence.get("target_id")
                edge_kind = evidence.get("kind")
                if target_id and edge_kind:
                    edge_ref = {"source_id": source_id, "target_id": target_id, "kind": edge_kind}
                    if edge_ref not in edge_refs:
                        edge_refs.append(edge_ref)
    version_ids = _version_ids_for(graph, tuple(node_ids))
    return {
        "nodes": node_ids,
        "edges": edge_refs,
        "versions": list(version_ids),
        "occurrences": occurrence_ids,
    }


def _version_ids_for(graph: StructuralHarnessGraph, entity_node_ids: tuple[str, ...]) -> tuple[str, ...]:
    entity_set = set(entity_node_ids)
    version_ids: list[str] = []
    for edge in graph.edges:
        if edge.kind == "VERSION_OF" and edge.target_id in entity_set:
            _append_unique(version_ids, edge.source_id)
    return tuple(version_ids)


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


__all__ = ["answer_structural_query"]
