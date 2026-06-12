from __future__ import annotations

from .anchor_resolution import resolve_anchors
from .identity import harness_card_id
from .models import HarnessCard
from .models import HarnessNextAction
from .models import HarnessQueryRequest
from .models import HarnessQueryResponse
from .models import ResolvedAnchor
from .models import StructuralHarnessGraph
from .projection import HarnessProjectionDocument
from .projection import build_projection_documents
from .relations import HistoricalRelationCandidate
from .relations import HistoricalRelationPolicy
from .relations import historical_relation_candidates
from .retrieval import LexicalRetrievalOptions
from .retrieval import LexicalRetrievalHit
from .retrieval import VectorRetrievalOptions
from .retrieval import VectorRetrievalHit
from .retrieval import search_projection_documents
from .retrieval import search_projection_documents_vector
from .selection import CardSelectionOptions
from .selection import select_harness_cards


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
    candidate_cards: list[HarnessCard] = []
    seen_cards = set(request.already_seen_card_ids)
    seen_node_ids = set(request.already_seen_node_ids)
    max_cards = max(1, int(request.max_cards or 5))
    candidate_limit = _candidate_limit(max_cards)
    for anchor in anchors.resolved:
        node = node_by_id.get(anchor.node_id)
        if node is None or node.id in seen_node_ids:
            continue
        card = _card_for_anchor(graph=graph, request=request, intent=intent_used, anchor=anchor)
        if card.card_id in seen_cards:
            continue
        candidate_cards.append(card)
        if len(candidate_cards) >= candidate_limit:
            break
    for anchor in anchors.resolved:
        if len(candidate_cards) >= candidate_limit:
            break
        for doc_card in _doc_support_cards_for_anchor(
            graph=graph,
            request=request,
            intent=intent_used,
            anchor_node_id=anchor.node_id,
            seen_cards=seen_cards,
            seen_node_ids=seen_node_ids,
        ):
            candidate_cards.append(doc_card)
            if len(candidate_cards) >= candidate_limit:
                break
    for anchor in anchors.resolved:
        if len(candidate_cards) >= candidate_limit:
            break
        for dependency_card in _dependency_cards_for_anchor(
            graph=graph,
            request=request,
            intent=intent_used,
            anchor_node_id=anchor.node_id,
            seen_cards=seen_cards,
            seen_node_ids=seen_node_ids,
        ):
            candidate_cards.append(dependency_card)
            if len(candidate_cards) >= candidate_limit:
                break
    for anchor in anchors.resolved:
        if len(candidate_cards) >= candidate_limit:
            break
        for historical_card in _historical_relation_cards_for_anchor(
            graph=graph,
            request=request,
            intent=intent_used,
            anchor_node_id=anchor.node_id,
            seen_cards=seen_cards,
            seen_node_ids=seen_node_ids,
            seen_relation_ids=set(request.already_seen_relation_ids),
            policy=historical_relation_policy,
        ):
            candidate_cards.append(historical_card)
            if len(candidate_cards) >= candidate_limit:
                break
    for anchor in anchors.resolved:
        if len(candidate_cards) >= candidate_limit:
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
            seen_node_ids=seen_node_ids,
        ):
            candidate_cards.append(child_card)
            if len(candidate_cards) >= candidate_limit:
                break

    cards = list(_select_cards(candidate_cards, request=request, max_cards=max_cards))
    selected_node_ids = _card_node_ids(tuple(cards))
    projection_documents: tuple[HarnessProjectionDocument, ...] | None = None

    lexical_used = False
    if len(cards) < max_cards:
        projection_documents = build_projection_documents(graph)
        lexical_cards = _lexical_candidate_cards(
            graph=graph,
            request=request,
            intent=intent_used,
            projection_documents=projection_documents,
            seen_cards=seen_cards,
            seen_node_ids=seen_node_ids | selected_node_ids,
            max_cards=candidate_limit,
        )
        if lexical_cards:
            candidate_cards.extend(lexical_cards)
            cards = list(_select_cards(candidate_cards, request=request, max_cards=max_cards))
            selected_node_ids = _card_node_ids(tuple(cards))
            lexical_used = _has_lexical_card(tuple(cards))

    vector_used = False
    if len(cards) < max_cards and _should_use_vector_candidates(anchors.resolved, anchors.unresolved, lexical_used):
        if projection_documents is None:
            projection_documents = build_projection_documents(graph)
        vector_cards = _vector_candidate_cards(
            graph=graph,
            request=request,
            intent=intent_used,
            projection_documents=projection_documents,
            seen_cards=seen_cards,
            seen_node_ids=seen_node_ids | selected_node_ids,
            max_cards=candidate_limit,
        )
        if vector_cards:
            candidate_cards.extend(vector_cards)
            cards = list(_select_cards(candidate_cards, request=request, max_cards=max_cards))
            vector_used = _has_vector_card(tuple(cards))

    actions = tuple(_next_action_for_card(card) for card in cards)
    if status == "unavailable" and (lexical_used or vector_used):
        status = "partial_structural"
    if status == "partial_structural":
        warnings.append("structural_only:no_work_history_or_semantic_reasoning_attached")
    if lexical_used:
        warnings.append("candidate_discovery:lexical_projection")
    if vector_used:
        warnings.append("candidate_discovery:vector_projection")
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


def _select_cards(candidates: list[HarnessCard], *, request: HarnessQueryRequest, max_cards: int) -> tuple[HarnessCard, ...]:
    result = select_harness_cards(
        tuple(candidates),
        options=CardSelectionOptions(
            max_cards=max_cards,
            max_tokens=request.max_tokens,
            already_seen_card_ids=request.already_seen_card_ids,
            already_seen_node_ids=request.already_seen_node_ids,
        ),
    )
    return result.selected


def _candidate_limit(max_cards: int) -> int:
    return max(8, max_cards * 4)


def _card_node_ids(cards: tuple[HarnessCard, ...]) -> set[str]:
    return {node_id for card in cards for evidence in card.evidence if (node_id := evidence.get("node_id"))}


def _has_lexical_card(cards: tuple[HarnessCard, ...]) -> bool:
    return any(
        evidence.get("kind") == "ProjectionDocument" and not evidence.get("retrieval_source")
        for card in cards
        for evidence in card.evidence
    )


def _has_vector_card(cards: tuple[HarnessCard, ...]) -> bool:
    return any(evidence.get("retrieval_source") == "vector" for card in cards for evidence in card.evidence)


def _should_use_vector_candidates(resolved: tuple[ResolvedAnchor, ...], unresolved: tuple[str, ...], lexical_used: bool) -> bool:
    if unresolved and not resolved:
        return False
    if resolved and not unresolved:
        return False
    return not lexical_used


def _lexical_candidate_cards(
    *,
    graph: StructuralHarnessGraph,
    request: HarnessQueryRequest,
    intent: str,
    projection_documents: tuple[HarnessProjectionDocument, ...],
    seen_cards: set[str],
    seen_node_ids: set[str],
    max_cards: int,
) -> tuple[HarnessCard, ...]:
    query_text = _task_text_for(request)
    if not query_text.strip() or max_cards <= 0:
        return ()
    node_by_id = graph.node_by_id()
    hits = search_projection_documents(
        projection_documents,
        query_text,
        options=LexicalRetrievalOptions(top_k=max_cards * 4),
    )
    out: list[HarnessCard] = []
    for hit in hits:
        source_node = node_by_id.get(hit.document.source_node_id)
        if source_node is None or source_node.id in seen_node_ids:
            continue
        card_id = harness_card_id(graph.repo_id, request.session_id, intent, ("lexical_projection", hit.document.doc_id))
        if card_id in seen_cards:
            continue
        out.append(_lexical_candidate_card(source_node=source_node, hit=hit, card_id=card_id))
        seen_node_ids.add(source_node.id)
        if len(out) >= max_cards:
            break
    return tuple(out)


def _vector_candidate_cards(
    *,
    graph: StructuralHarnessGraph,
    request: HarnessQueryRequest,
    intent: str,
    projection_documents: tuple[HarnessProjectionDocument, ...],
    seen_cards: set[str],
    seen_node_ids: set[str],
    max_cards: int,
) -> tuple[HarnessCard, ...]:
    query_text = _task_text_for(request)
    if not query_text.strip() or max_cards <= 0:
        return ()
    node_by_id = graph.node_by_id()
    hits = search_projection_documents_vector(
        projection_documents,
        query_text,
        options=VectorRetrievalOptions(top_k=max_cards * 4),
    )
    out: list[HarnessCard] = []
    for hit in hits:
        source_node = node_by_id.get(hit.document.source_node_id)
        if source_node is None or source_node.id in seen_node_ids:
            continue
        card_id = harness_card_id(graph.repo_id, request.session_id, intent, ("vector_projection", hit.document.doc_id))
        if card_id in seen_cards:
            continue
        out.append(_vector_candidate_card(source_node=source_node, hit=hit, card_id=card_id))
        seen_node_ids.add(source_node.id)
        if len(out) >= max_cards:
            break
    return tuple(out)


def _vector_candidate_card(*, source_node: object, hit: VectorRetrievalHit, card_id: str) -> HarnessCard:
    node_kind = str(getattr(source_node, "kind", ""))
    node_label = str(getattr(source_node, "label", ""))
    path = str(getattr(source_node, "metadata", {}).get("path") or node_label)
    card_type = {
        "File": "next_file",
        "Symbol": "symbol_context",
        "DocSection": "doc_support",
        "DocString": "doc_support",
    }.get(node_kind, "dependency")
    confidence = round(min(0.62, 0.36 + hit.score * 0.32), 2)
    features = ", ".join(hit.matched_features[:4])
    why = "Vector projection matched graph-grounded summary features."
    if features:
        why = f"Vector projection matched graph-grounded features: {features}."
    return HarnessCard(
        card_id=card_id,
        type=card_type,
        title=f"Inspect {node_label}",
        why=why,
        evidence=(
            {"node_id": str(getattr(source_node, "id", "")), "kind": node_kind, "path": path},
            {
                "doc_id": hit.document.doc_id,
                "kind": "ProjectionDocument",
                "source_kind": hit.document.source_kind,
                "doc_type": hit.document.doc_type,
                "retrieval_source": "vector",
                "embedding_method": hit.embedding_method,
                "score": f"{hit.score:.4f}",
                "matched_features": ", ".join(hit.matched_features),
            },
        ),
        risk="Vector candidate discovery only; inspect graph-grounded source before editing.",
        confidence=confidence,
        next_action=f"Open {path} and inspect {node_label} before editing.",
    )


def _lexical_candidate_card(*, source_node: object, hit: LexicalRetrievalHit, card_id: str) -> HarnessCard:
    node_kind = str(getattr(source_node, "kind", ""))
    node_label = str(getattr(source_node, "label", ""))
    path = str(getattr(source_node, "metadata", {}).get("path") or node_label)
    card_type = {
        "File": "next_file",
        "Symbol": "symbol_context",
        "DocSection": "doc_support",
        "DocString": "doc_support",
    }.get(node_kind, "dependency")
    matched = ", ".join(hit.matched_terms)
    confidence = round(min(0.7, 0.42 + hit.normalized_score * 0.28), 2)
    return HarnessCard(
        card_id=card_id,
        type=card_type,
        title=f"Inspect {node_label}",
        why=f"Lexical projection matched {matched} in graph-grounded {hit.document.doc_type}.",
        evidence=(
            {"node_id": str(getattr(source_node, "id", "")), "kind": node_kind, "path": path},
            {
                "doc_id": hit.document.doc_id,
                "kind": "ProjectionDocument",
                "source_kind": hit.document.source_kind,
                "doc_type": hit.document.doc_type,
                "score": f"{hit.normalized_score:.4f}",
                "matched_terms": matched,
            },
        ),
        risk="Candidate discovery only; inspect graph-grounded source before editing.",
        confidence=confidence,
        next_action=f"Open {path} and inspect {node_label} before editing.",
    )


def _doc_support_cards_for_anchor(
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
    out: list[HarnessCard] = []
    semantic_edges = sorted(
        (
            edge
            for edge in graph.edges
            if edge.target_id == anchor_node_id
            and edge.kind in {"DOCUMENTS_FILE", "DOCUMENTS_SYMBOL", "MENTIONS_FILE", "MENTIONS_SYMBOL"}
        ),
        key=lambda edge: (_doc_edge_rank(edge.kind), -float(edge.confidence or 0.0), edge.source_id),
    )
    for edge in semantic_edges:
        doc_node = node_by_id.get(edge.source_id)
        if doc_node is None or doc_node.kind not in {"DocSection", "DocString"} or doc_node.id in seen_node_ids:
            continue
        card_id = harness_card_id(graph.repo_id, request.session_id, intent, (anchor_node_id, doc_node.id, edge.kind))
        if card_id in seen_cards:
            continue
        out.append(_doc_support_card(anchor=anchor, doc_node=doc_node, edge=edge, card_id=card_id))
    return tuple(out)


def _doc_support_card(*, anchor: object, doc_node: object, edge: object, card_id: str) -> HarnessCard:
    anchor_label = str(getattr(anchor, "label", ""))
    doc_label = str(getattr(doc_node, "label", ""))
    doc_path = str(getattr(doc_node, "metadata", {}).get("path") or doc_label)
    edge_kind = str(getattr(edge, "kind", ""))
    confidence = round(min(0.86, float(getattr(edge, "confidence", 0.0) or 0.0)), 2)
    return HarnessCard(
        card_id=card_id,
        type="doc_support",
        title=f"Use docs for {anchor_label}",
        why=f"{doc_label} {edge_kind.lower().replace('_', ' ')} {anchor_label}.",
        evidence=(
            {"node_id": str(getattr(doc_node, "id", "")), "kind": str(getattr(doc_node, "kind", "")), "path": doc_path},
            {"node_id": str(getattr(anchor, "id", "")), "kind": str(getattr(anchor, "kind", ""))},
            {
                "source_id": str(getattr(edge, "source_id", "")),
                "target_id": str(getattr(edge, "target_id", "")),
                "kind": edge_kind,
                "confidence": f"{confidence:.2f}",
            },
        ),
        risk="Doc-backed semantic support; verify code behavior before editing.",
        confidence=confidence,
        next_action=f"Read {doc_path} for documented constraints around {anchor_label}.",
    )


def _doc_edge_rank(kind: str) -> int:
    return {"DOCUMENTS_SYMBOL": 0, "DOCUMENTS_FILE": 1, "MENTIONS_SYMBOL": 2, "MENTIONS_FILE": 3}.get(kind, 9)


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
    for candidate in historical_relation_candidates(
        graph,
        anchor_node_id=anchor_node_id,
        task_text=_task_text_for(request),
        policy=policy,
    ):
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
    for match in candidate.occurrence_matches:
        occurrence = match.node
        metadata = occurrence.metadata
        out.append(
            {
                "node_id": occurrence.id,
                "kind": occurrence.kind,
                "commit_id": str(metadata.get("commit_id") or ""),
                "work_window_id": str(metadata.get("work_window_id") or ""),
                "reason_status": str(metadata.get("reason_status") or "semantic_pending"),
                "task_relevance": match.relevance_status,
                "matched_terms": ",".join(match.matched_terms),
            }
        )
    return tuple(out)


def _task_text_for(request: HarnessQueryRequest) -> str:
    recent_tool_result = " ".join(str(value) for value in request.recent_tool_result.values())
    return " ".join(
        (
            request.user_goal,
            " ".join(request.files),
            " ".join(request.symbols),
            " ".join(request.errors),
            recent_tool_result,
        )
    )


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
    if card.type == "doc_support":
        target = card.title
        if card.evidence:
            target = card.evidence[0].get("path") or card.evidence[0].get("node_id", card.title)
        return HarnessNextAction(
            action_type="inspect_file",
            target=target,
            reason=card.why,
            priority="recommended",
        )
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
        target=_next_action_target(card),
        reason=card.why,
        priority="recommended",
    )


def _next_action_target(card: HarnessCard) -> str:
    if card.next_action.startswith("Open "):
        return card.next_action.removeprefix("Open ").split(" and inspect", 1)[0]
    for evidence in card.evidence:
        if path := evidence.get("path"):
            return path
    return card.evidence[0].get("node_id", card.title) if card.evidence else card.title


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
