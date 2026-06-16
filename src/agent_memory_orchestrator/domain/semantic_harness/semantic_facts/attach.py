from __future__ import annotations

from dataclasses import dataclass

from ..models import HarnessNode
from ..store import HarnessGraphStore
from .models import REVIEW_ACCEPTED
from .models import SYSTEM_SCOPE
from .models import SemanticFact


@dataclass(slots=True, frozen=True)
class SemanticFactAttachResult:
    attached_fact_ids: tuple[str, ...]
    updated_node_ids: tuple[str, ...]
    skipped_fact_ids: tuple[str, ...]
    diagnostics: tuple[dict[str, str], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "attached_fact_ids": list(self.attached_fact_ids),
            "updated_node_ids": list(self.updated_node_ids),
            "skipped_fact_ids": list(self.skipped_fact_ids),
            "diagnostics": list(self.diagnostics),
        }


def attach_reviewed_facts_to_store(
    store: HarnessGraphStore,
    *,
    facts: tuple[SemanticFact, ...],
) -> SemanticFactAttachResult:
    attached: list[str] = []
    updated_nodes: set[str] = set()
    skipped: list[str] = []
    diagnostics: list[dict[str, str]] = []

    for fact in facts:
        if fact.review_status != REVIEW_ACCEPTED:
            skipped.append(fact.fact_id)
            diagnostics.append(_diag(fact, "fact_not_accepted"))
            continue
        if fact.fact_scope == SYSTEM_SCOPE and not fact.anchor_node_ids:
            skipped.append(fact.fact_id)
            diagnostics.append(_diag(fact, "system_fact_requires_future_graph_level_store"))
            continue
        targets = tuple(node_id for node_id in fact.anchor_node_ids if store.node_exists(node_id))
        if not targets:
            skipped.append(fact.fact_id)
            diagnostics.append(_diag(fact, "no_existing_anchor_nodes"))
            continue
        for node_id in targets:
            node = store.get_node(node_id)
            if node is None:
                continue
            store.replace_node(_node_with_fact(node, fact))
            updated_nodes.add(node.id)
        attached.append(fact.fact_id)

    return SemanticFactAttachResult(
        attached_fact_ids=tuple(dict.fromkeys(attached)),
        updated_node_ids=tuple(sorted(updated_nodes)),
        skipped_fact_ids=tuple(dict.fromkeys(skipped)),
        diagnostics=tuple(diagnostics),
    )


def _node_with_fact(node: HarnessNode, fact: SemanticFact) -> HarnessNode:
    metadata = dict(node.metadata)
    existing = tuple(item for item in metadata.get("semantic_facts", ()) if isinstance(item, dict))
    fact_payload = fact.as_dict()
    next_facts = _replace_fact(existing, fact_payload)
    metadata["semantic_facts"] = list(next_facts)
    return HarnessNode(
        id=node.id,
        kind=node.kind,
        label=node.label,
        repo_id=node.repo_id,
        status=node.status,
        summary=node.summary,
        metadata=metadata,
    )


def _replace_fact(existing: tuple[dict[str, object], ...], fact_payload: dict[str, object]) -> tuple[dict[str, object], ...]:
    fact_id = str(fact_payload.get("fact_id") or "")
    kept = tuple(item for item in existing if str(item.get("fact_id") or "") != fact_id)
    return (*kept, fact_payload)


def _diag(fact: SemanticFact, reason: str) -> dict[str, str]:
    return {"fact_id": fact.fact_id, "reason": reason, "fact_scope": fact.fact_scope}


__all__ = ["SemanticFactAttachResult", "attach_reviewed_facts_to_store"]
