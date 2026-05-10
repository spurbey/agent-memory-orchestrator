from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .config import Settings
from .graph_store import GraphEdge, GraphNode, GraphStore
from .qwen_client import OllamaQwenClient, QwenUnavailable
from .versioning import GitCommitDetails, GitDiffSummary, VersionBackend


ANSWER_GRADE_PROMOTION_KINDS = {"Decision", "WorkChange", "Fix", "Bug", "Blocker", "TestRun", "ContextSnapshot"}
SUPPORT_ONLY_KINDS = {
    "RawEvidenceRef",
    "Prompt",
    "ToolResult",
    "ToolUse",
    "CleanedEvidenceWindow",
    "GraphDelta",
    "Session",
    "Repo",
    "Branch",
    "File",
    "Topic",
}
VERSION_RELATIONS = {"DUPLICATE_OF", "REFINES", "SUPERSEDES", "CONTRADICTS"}
MERGE_CLASSIFIER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "relation": {
            "type": "string",
            "enum": ["NEW", "DUPLICATE_OF", "REFINES", "SUPERSEDES", "CONTRADICTS"],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
    "required": ["relation", "confidence", "reason"],
    "additionalProperties": False,
}


class MergeClassifier(Protocol):
    def classify(self, draft: dict[str, Any], candidate: dict[str, Any], score: dict[str, Any]) -> dict[str, Any]:
        """Classify a merge relation for an ambiguous node pair."""


@dataclass(slots=True, frozen=True)
class MergeCandidate:
    source_id: str
    target_id: str
    relation: str
    confidence: float
    reason: str
    source: str = "deterministic"
    score: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "relation": self.relation,
            "confidence": round(float(self.confidence), 6),
            "reason": self.reason,
            "source": self.source,
            "score": self.score,
        }


class QwenMergeClassifier:
    """Qwen-backed relation classifier for ambiguous merge candidates."""

    def __init__(self, settings: Settings) -> None:
        timeout = min(settings.qwen_timeout_seconds, settings.qwen_extract_timeout_seconds)
        self.client = OllamaQwenClient(
            endpoint=settings.qwen_endpoint,
            model=settings.qwen_model,
            timeout_seconds=timeout,
            num_ctx=settings.qwen_num_ctx,
        )
        self.timeout_seconds = timeout

    def classify(self, draft: dict[str, Any], candidate: dict[str, Any], score: dict[str, Any]) -> dict[str, Any]:
        prompt = (
            "/no_think\n"
            "Classify the relationship between a new AMO draft graph node and an existing central node. "
            "Return only JSON with relation, confidence, and reason. "
            "Use NEW when there is no durable semantic relation. "
            "Use DUPLICATE_OF only for the same meaning. "
            "Use REFINES when the new node adds detail without invalidating the old node. "
            "Use SUPERSEDES when the new node replaces the old node. "
            "Use CONTRADICTS when both cannot be true.\n"
            f"score={json.dumps(score, ensure_ascii=False, sort_keys=True)}\n"
            f"draft={json.dumps(_classifier_node(draft), ensure_ascii=False, sort_keys=True)}\n"
            f"candidate={json.dumps(_classifier_node(candidate), ensure_ascii=False, sort_keys=True)}"
        )
        payload = self.client._generate_json(  # noqa: SLF001 - package-local schema JSON adapter.
            prompt,
            num_predict=220,
            timeout_seconds=self.timeout_seconds,
            schema=MERGE_CLASSIFIER_SCHEMA,
        )
        relation = str(payload.get("relation") or "NEW").upper()
        if relation not in {"NEW", *VERSION_RELATIONS}:
            relation = "NEW"
        return {
            "relation": relation,
            "confidence": _float(payload.get("confidence"), default=0.0),
            "reason": _trim(str(payload.get("reason") or ""), 220),
        }


class CommitMergeEngine:
    """Promotes session draft graph work into the central graph at commit/finalize boundaries."""

    def __init__(
        self,
        settings: Settings,
        store: GraphStore,
        version_backend: VersionBackend,
        *,
        classifier: MergeClassifier | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.version_backend = version_backend
        self.classifier = classifier

    def finalize_session(
        self,
        *,
        session_id: str,
        commit: str = "HEAD",
        apply: bool = False,
        limit: int = 500,
        cwd: str | Path | None = None,
    ) -> dict[str, Any]:
        session_id = str(session_id or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        safe_limit = max(1, min(5000, int(limit)))
        commit_info = self._commit_details(commit, cwd)
        diff_info = self._diff_summary(commit, cwd)
        resolved_commit = commit_info.commit or diff_info.target or commit
        patch_id = self._patch_id(commit, cwd)
        evidence_id = ""

        draft_nodes = [
            node
            for node in self.store.list_nodes(kinds=sorted(ANSWER_GRADE_PROMOTION_KINDS), session_id=session_id, limit=safe_limit)
            if str(node.get("status") or "draft") == "draft" and _is_promotable(node)
        ]
        skipped_nodes = [
            node
            for node in self.store.list_nodes(session_id=session_id, limit=safe_limit)
            if str(node.get("kind") or "") in SUPPORT_ONLY_KINDS
            or (str(node.get("kind") or "") in ANSWER_GRADE_PROMOTION_KINDS and not _is_promotable(node))
        ]
        if draft_nodes:
            evidence_id = str(draft_nodes[0].get("evidence_id") or "")
        commit_node = self._commit_node(
            session_id=session_id,
            commit_id=resolved_commit,
            commit=commit_info.as_dict(),
            diff=diff_info.as_dict(),
            patch_id=patch_id,
            evidence_id=evidence_id,
        )

        central_candidates = [
            node
            for node in self.store.list_nodes(kinds=sorted(ANSWER_GRADE_PROMOTION_KINDS), limit=max(1000, safe_limit * 4))
            if _is_existing_central_candidate(node, session_id=session_id)
        ]
        relations, review = self._classify_relations(draft_nodes, central_candidates)
        planned_promotions = [
            self._promoted_node(node, commit_id=resolved_commit, patch_id=patch_id, commit=commit_info.as_dict(), diff=diff_info.as_dict())
            for node in draft_nodes
        ]

        result: dict[str, Any] = {
            "ok": True,
            "apply": bool(apply),
            "session_id": session_id,
            "commit": commit_info.as_dict(),
            "diff": diff_info.as_dict(),
            "commit_id": resolved_commit,
            "patch_id": patch_id,
            "draft_count": len(draft_nodes),
            "skipped_support_count": len(skipped_nodes),
            "promoted_count": len(planned_promotions) if apply else 0,
            "planned_promotions": [_node_summary(node) for node in planned_promotions],
            "relations": [relation.as_dict() for relation in relations],
            "review_candidates": [candidate.as_dict() for candidate in review],
            "skipped": [_node_summary(node) for node in skipped_nodes[:50]],
            "edges_written": 0,
            "statuses_updated": 0,
        }
        if not apply:
            return result

        self.store.upsert_node(commit_node)
        edges_written = 0
        statuses_updated = 0
        for node in planned_promotions:
            self.store.upsert_node(node)
            evidence = node.evidence_id or evidence_id
            self.store.upsert_edge(
                GraphEdge(
                    id=f"edge:{node.id}:COMMITTED_AS:{commit_node.id}",
                    source_id=node.id,
                    target_id=commit_node.id,
                    kind="COMMITTED_AS",
                    confidence=1.0,
                    evidence_id=evidence,
                    metadata={"patch_id": patch_id},
                )
            )
            edges_written += 1
            for file_path in _node_files(node.as_dict(), diff_info.changed_files):
                file_node = GraphNode(
                    id=f"file:{file_path}",
                    kind="File",
                    label=file_path,
                    summary=f"File touched by AMO work ledger: {file_path}",
                    status="active",
                    scope="central",
                    project_id=self.settings.project_id,
                    source_app=node.source_app,
                )
                self.store.upsert_node(file_node)
                self.store.upsert_edge(
                    GraphEdge(
                        id=f"edge:{node.id}:MODIFIES:{file_node.id}",
                        source_id=node.id,
                        target_id=file_node.id,
                        kind="MODIFIES",
                        confidence=1.0,
                        evidence_id=evidence,
                        metadata={"commit_id": resolved_commit, "patch_id": patch_id},
                    )
                )
                edges_written += 1
        self.store.upsert_edge(
            GraphEdge(
                id=f"edge:session:{session_id}:MERGED_INTO:{commit_node.id}",
                source_id=f"session:{session_id}",
                target_id=commit_node.id,
                kind="MERGED_INTO",
                confidence=1.0,
                evidence_id=evidence_id,
                metadata={"patch_id": patch_id, "promoted_count": len(planned_promotions)},
            )
        )
        edges_written += 1
        for relation in relations:
            self.store.upsert_edge(
                GraphEdge(
                    id=f"edge:{relation.source_id}:{relation.relation}:{relation.target_id}",
                    source_id=relation.source_id,
                    target_id=relation.target_id,
                    kind=relation.relation,
                    confidence=relation.confidence,
                    evidence_id=evidence_id,
                    metadata={
                        "reason": relation.reason,
                        "source": relation.source,
                        "score": relation.score,
                        "commit_id": resolved_commit,
                    },
                )
            )
            edges_written += 1
            if relation.relation == "SUPERSEDES" and self.store.set_node_status(relation.target_id, "superseded"):
                statuses_updated += 1
        result["promoted_count"] = len(planned_promotions)
        result["edges_written"] = edges_written
        result["statuses_updated"] = statuses_updated
        return result

    def _commit_details(self, commit: str, cwd: str | Path | None) -> GitCommitDetails:
        try:
            return self.version_backend.commit_details(commit, cwd)
        except AttributeError:
            return GitCommitDetails(available=False, commit=commit, error="version_backend_missing_commit_details")

    def _diff_summary(self, commit: str, cwd: str | Path | None) -> GitDiffSummary:
        try:
            return self.version_backend.diff_summary(commit, cwd)
        except AttributeError:
            return GitDiffSummary(available=False, target=commit, error="version_backend_missing_diff_summary")

    def _patch_id(self, commit: str, cwd: str | Path | None) -> str:
        try:
            return self.version_backend.patch_id(commit, cwd)
        except AttributeError:
            return ""

    def _classify_relations(
        self,
        draft_nodes: list[dict[str, Any]],
        central_candidates: list[dict[str, Any]],
    ) -> tuple[list[MergeCandidate], list[MergeCandidate]]:
        relations: list[MergeCandidate] = []
        review: list[MergeCandidate] = []
        for draft in draft_nodes:
            best: MergeCandidate | None = None
            for candidate in central_candidates:
                score = _candidate_score(draft, candidate)
                relation = _deterministic_relation(draft, candidate, score)
                if relation.relation == "NEW" and _is_ambiguous(score):
                    qwen_relation = self._qwen_relation(draft, candidate, score)
                    if qwen_relation is not None:
                        relation = qwen_relation
                if relation.relation == "NEW":
                    continue
                if relation.source == "qwen" and relation.confidence < 0.70:
                    review.append(relation)
                    continue
                if best is None or relation.confidence > best.confidence:
                    best = relation
            if best is not None:
                relations.append(best)
        return relations, review

    def _qwen_relation(
        self,
        draft: dict[str, Any],
        candidate: dict[str, Any],
        score: dict[str, Any],
    ) -> MergeCandidate | None:
        if self.classifier is None:
            return None
        try:
            payload = self.classifier.classify(draft, candidate, score)
        except QwenUnavailable:
            return None
        relation = str(payload.get("relation") or "NEW").upper()
        if relation not in VERSION_RELATIONS:
            return None
        return MergeCandidate(
            source_id=str(draft.get("id") or ""),
            target_id=str(candidate.get("id") or ""),
            relation=relation,
            confidence=_float(payload.get("confidence"), default=0.0),
            reason=str(payload.get("reason") or "qwen merge classification"),
            source="qwen",
            score=score,
        )

    def _commit_node(
        self,
        *,
        session_id: str,
        commit_id: str,
        commit: dict[str, Any],
        diff: dict[str, Any],
        patch_id: str,
        evidence_id: str,
    ) -> GraphNode:
        label = commit_id[:12] if commit_id else "manual-finalize"
        subject = str(commit.get("subject") or "")
        summary = f"Git commit {label} promoted AMO session work"
        if subject:
            summary = f"{summary}: {subject}"
        return GraphNode(
            id=f"commit:{commit_id or uuid.uuid4().hex}",
            kind="GitCommit",
            label=label,
            summary=summary,
            status="committed",
            scope="central",
            session_id=session_id,
            project_id=self.settings.project_id,
            source_app="codex",
            evidence_id=evidence_id,
            commit_id=commit_id,
            metadata={"commit": commit, "diff": diff, "patch_id": patch_id},
        )

    def _promoted_node(
        self,
        node: dict[str, Any],
        *,
        commit_id: str,
        patch_id: str,
        commit: dict[str, Any],
        diff: dict[str, Any],
    ) -> GraphNode:
        metadata = dict(node.get("metadata") if isinstance(node.get("metadata"), dict) else {})
        merge = dict(metadata.get("merge") if isinstance(metadata.get("merge"), dict) else {})
        merge.update(
            {
                "promoted_at": datetime.now(timezone.utc).isoformat(),
                "commit_id": commit_id,
                "patch_id": patch_id,
                "source_status": node.get("status") or "draft",
            }
        )
        metadata["merge"] = merge
        metadata.setdefault("commit", commit)
        metadata.setdefault("diff", diff)
        return GraphNode(
            id=str(node.get("id") or ""),
            kind=str(node.get("kind") or ""),
            label=str(node.get("label") or ""),
            summary=str(node.get("summary") or ""),
            status="committed",
            scope="central",
            session_id=str(node.get("session_id") or ""),
            project_id=str(node.get("project_id") or self.settings.project_id),
            source_app=str(node.get("source_app") or "codex"),
            evidence_id=str(node.get("evidence_id") or ""),
            commit_id=commit_id,
            created_at=str(node.get("created_at") or ""),
            metadata=metadata,
        )


def _is_existing_central_candidate(node: dict[str, Any], *, session_id: str) -> bool:
    if str(node.get("session_id") or "") == session_id and str(node.get("status") or "") == "draft":
        return False
    if str(node.get("kind") or "") not in ANSWER_GRADE_PROMOTION_KINDS:
        return False
    return str(node.get("scope") or "") == "central" or str(node.get("status") or "") in {
        "committed",
        "active",
        "superseded",
    }


def _is_promotable(node: dict[str, Any]) -> bool:
    kind = str(node.get("kind") or "")
    if kind not in ANSWER_GRADE_PROMOTION_KINDS:
        return False
    text = f"{node.get('label') or ''} {node.get('summary') or ''}".strip()
    if len(text) < 16:
        return kind == "TestRun" and bool(text)
    lowered = text.lower()
    noisy_prefixes = ('"continue":', "{", "[", "from __future__", "import ", "class ", "def ", "raise ", "assert ")
    noisy_terms = ("hook_event_name", "status_porcelain", "captureonly", "manualsmoke", "after_preview")
    return not lowered.startswith(noisy_prefixes) and not any(term in lowered for term in noisy_terms)


def _candidate_score(draft: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    draft_text = _node_text(draft)
    candidate_text = _node_text(candidate)
    draft_terms = _terms(draft_text)
    candidate_terms = _terms(candidate_text)
    overlap = draft_terms & candidate_terms
    union = draft_terms | candidate_terms
    text_similarity = len(overlap) / max(1, len(union))
    same_kind = str(draft.get("kind") or "") == str(candidate.get("kind") or "")
    draft_files = set(_node_files(draft))
    candidate_files = set(_node_files(candidate))
    shared_files = sorted(draft_files & candidate_files)
    file_similarity = len(shared_files) / max(1, len(draft_files | candidate_files))
    shared_topic = bool(overlap & {"graph", "graphrag", "qwen", "merge", "commit", "session", "evidence", "cleaning"})
    total = text_similarity * 0.58 + file_similarity * 0.24 + (0.12 if same_kind else 0.0) + (0.06 if shared_topic else 0.0)
    return {
        "total": round(total, 6),
        "text_similarity": round(text_similarity, 6),
        "file_similarity": round(file_similarity, 6),
        "same_kind": same_kind,
        "shared_files": shared_files,
        "shared_terms": sorted(overlap)[:16],
    }


def _deterministic_relation(draft: dict[str, Any], candidate: dict[str, Any], score: dict[str, Any]) -> MergeCandidate:
    draft_text = _normalize_text(_node_text(draft))
    candidate_text = _normalize_text(_node_text(candidate))
    total = float(score.get("total") or 0.0)
    source_id = str(draft.get("id") or "")
    target_id = str(candidate.get("id") or "")
    if draft_text and draft_text == candidate_text:
        return MergeCandidate(source_id, target_id, "DUPLICATE_OF", 0.99, "same normalized text", score=score)
    if _contradicts(draft_text, candidate_text) and total >= 0.40:
        return MergeCandidate(source_id, target_id, "CONTRADICTS", max(0.72, total), "opposing policy terms", score=score)
    if _supersedes(draft_text) and total >= 0.45:
        return MergeCandidate(source_id, target_id, "SUPERSEDES", max(0.74, total), "new node uses replacement language", score=score)
    if total >= 0.82:
        return MergeCandidate(source_id, target_id, "REFINES", total, "high semantic and file overlap", score=score)
    if total >= 0.68 and score.get("same_kind"):
        return MergeCandidate(source_id, target_id, "REFINES", total, "same kind with strong overlap", score=score)
    return MergeCandidate(source_id, target_id, "NEW", total, "below deterministic merge threshold", score=score)


def _is_ambiguous(score: dict[str, Any]) -> bool:
    total = float(score.get("total") or 0.0)
    return 0.42 <= total < 0.82


def _node_files(node: dict[str, Any], fallback: list[str] | None = None) -> list[str]:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    files = metadata.get("changed_files")
    if not isinstance(files, list):
        files = fallback or []
    return [str(file).replace("\\", "/") for file in files if str(file).strip()]


def _node_text(node: dict[str, Any]) -> str:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    fields = [
        node.get("kind"),
        node.get("label"),
        node.get("summary"),
        metadata.get("goal"),
        metadata.get("latest_decision"),
        metadata.get("next_step"),
        " ".join(_node_files(node)),
    ]
    return " ".join(str(field or "") for field in fields)


def _terms(text: str) -> set[str]:
    stop = {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "node",
        "nodes",
        "session",
        "work",
        "change",
        "changed",
    }
    return {term for term in re.sub(r"[^a-zA-Z0-9_.-]+", " ", text.lower()).split() if len(term) > 2 and term not in stop}


def _normalize_text(text: str) -> str:
    return " ".join(re.sub(r"[^a-zA-Z0-9_.-]+", " ", text.lower()).split())


def _contradicts(left: str, right: str) -> bool:
    opposing = (
        ("enable", "disable"),
        ("enabled", "disabled"),
        ("auto", "manual"),
        ("allow", "block"),
        ("include raw", "hide raw"),
        ("promote raw", "never promote raw"),
    )
    combined = f"{left}\n{right}"
    return any(a in combined and b in combined for a, b in opposing)


def _supersedes(text: str) -> bool:
    return any(term in text for term in ("supersede", "replace", "instead of", "no longer", "remove old", "retire"))


def _classifier_node(node: dict[str, Any]) -> dict[str, Any]:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return {
        "id": node.get("id"),
        "kind": node.get("kind"),
        "label": node.get("label"),
        "summary": node.get("summary"),
        "status": node.get("status"),
        "scope": node.get("scope"),
        "commit_id": node.get("commit_id"),
        "changed_files": _node_files(node),
        "goal": metadata.get("goal"),
        "latest_decision": metadata.get("latest_decision"),
        "next_step": metadata.get("next_step"),
    }


def _node_summary(node: GraphNode | dict[str, Any]) -> dict[str, Any]:
    row = node.as_dict() if isinstance(node, GraphNode) else node
    return {
        "id": row.get("id"),
        "kind": row.get("kind"),
        "label": row.get("label"),
        "summary": _trim(str(row.get("summary") or ""), 240),
        "status": row.get("status"),
        "scope": row.get("scope"),
        "session_id": row.get("session_id"),
        "commit_id": row.get("commit_id"),
        "evidence_id": row.get("evidence_id"),
    }


def _trim(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "..."


def _float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
