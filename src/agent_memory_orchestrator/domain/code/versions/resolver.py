from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from typing import Protocol

from ...reasoning.models import DecisionThread
from ..models import CodeNode


REVERT_SIGNAL_RE = re.compile(r"\b(revert|reverting|undo|roll back|rollback|restore|back out)\b", re.IGNORECASE)


class EmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]:
        """Return a normalized or comparable embedding for text."""


@dataclass(slots=True, frozen=True)
class CodeVersionRelation:
    source_id: str
    target_id: str
    kind: str
    confidence: float
    reason: str
    old_status: str = ""
    metadata: dict[str, object] | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "kind": self.kind,
            "confidence": self.confidence,
            "reason": self.reason,
            "old_status": self.old_status,
            "metadata": self.metadata or {},
        }


@dataclass(slots=True, frozen=True)
class CodeVersionPlan:
    new_node: CodeNode
    relations: tuple[CodeVersionRelation, ...]
    diagnostics: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "new_node": self.new_node.as_dict(),
            "relations": [relation.as_dict() for relation in self.relations],
            "diagnostics": list(self.diagnostics),
        }


def resolve_code_node_version(
    *,
    new_node: CodeNode,
    new_thread: DecisionThread,
    candidates: list[CodeNode],
    candidate_threads: dict[str, DecisionThread],
    embedder: EmbeddingProvider | None,
    threshold: float = 0.75,
) -> CodeVersionPlan:
    diagnostics: list[str] = []
    relations: list[CodeVersionRelation] = []
    resolved_new_node = new_node

    for old in candidates:
        if not _same_file(old.file_path, new_node.file_path):
            continue
        same_ast = _same_ast_family(old, new_node)
        overlaps = _overlaps(old.line_start, old.line_end, new_node.line_start, new_node.line_end)
        old_thread = candidate_threads.get(old.id)
        if old_thread is None:
            diagnostics.append(f"missing_thread:{old.id}")
            continue
        similarity = _thread_similarity(new_thread, old_thread, embedder)
        if similarity is None:
            diagnostics.append("embedding_status=missing")
            continue
        if similarity < threshold:
            diagnostics.append(f"unrelated_same_file:{old.id}:{similarity:.3f}")
            continue
        if not same_ast and not overlaps:
            diagnostics.append(f"same_topic_different_ast:{old.id}:{similarity:.3f}")
            continue

        relation = _relation_for(new_node, old, new_thread, similarity)
        if relation.kind in {"SUPERSEDED_BY", "REVERTS", "REFINES"} and not resolved_new_node.prev_content:
            resolved_new_node = replace(resolved_new_node, prev_content=old.content)
        relations.append(relation)

    return CodeVersionPlan(new_node=resolved_new_node, relations=tuple(relations), diagnostics=tuple(diagnostics))


def _relation_for(new_node: CodeNode, old: CodeNode, new_thread: DecisionThread, similarity: float) -> CodeVersionRelation:
    if _has_revert_signal(new_thread):
        return CodeVersionRelation(
            source_id=new_node.id,
            target_id=old.id,
            kind="REVERTS",
            confidence=round(similarity, 6),
            reason="same file/topic/AST with revert signal",
            old_status="superseded",
            metadata={"similarity": round(similarity, 6)},
        )
    if _replaces_content(old, new_node):
        return CodeVersionRelation(
            source_id=old.id,
            target_id=new_node.id,
            kind="SUPERSEDED_BY",
            confidence=round(similarity, 6),
            reason="same file/topic/AST and new content replaces old content",
            old_status="superseded",
            metadata={"similarity": round(similarity, 6)},
        )
    return CodeVersionRelation(
        source_id=new_node.id,
        target_id=old.id,
        kind="REFINES",
        confidence=round(similarity, 6),
        reason="same file/topic/AST and new content adds detail",
        old_status="refined",
        metadata={"similarity": round(similarity, 6)},
    )


def _thread_similarity(left: DecisionThread, right: DecisionThread, embedder: EmbeddingProvider | None) -> float | None:
    if embedder is None:
        return None
    left_text = " ".join([left.topic, *left.file_paths])
    right_text = " ".join([right.topic, *right.file_paths])
    return _cosine_similarity(embedder.embed(left_text), embedder.embed(right_text))


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _same_file(left: str, right: str) -> bool:
    return left.replace("\\", "/").strip().lower() == right.replace("\\", "/").strip().lower()


def _same_ast_family(left: CodeNode, right: CodeNode) -> bool:
    if left.ast_status.startswith("unparsed") or right.ast_status.startswith("unparsed"):
        return _overlaps(left.line_start, left.line_end, right.line_start, right.line_end)
    return left.ast_type == right.ast_type


def _overlaps(left_start: int, left_end: int, right_start: int, right_end: int) -> bool:
    return max(left_start, right_start) <= min(left_end, right_end)


def _has_revert_signal(thread: DecisionThread) -> bool:
    text = " ".join([thread.topic, str(thread.metadata)])
    return bool(REVERT_SIGNAL_RE.search(text))


def _replaces_content(old: CodeNode, new: CodeNode) -> bool:
    old_text = old.content.strip()
    new_text = new.content.strip()
    if old_text == new_text:
        return False
    if old_text and old_text in new_text:
        return False
    return True
