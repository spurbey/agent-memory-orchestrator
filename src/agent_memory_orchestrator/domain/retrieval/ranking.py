from __future__ import annotations

import json
import re
from typing import Any

from .classification import query_has_code_locator as _query_has_code_locator
from .models import RetrievalDocument
from .text import QUERY_STOPWORDS
from .text import expanded_query_terms as _expanded_query_terms
from .text import normalize as _normalize
from .text import stem_term as _stem_term
from .text import terms as _terms


VERSION_FLOW_OPERATOR_TERMS = {
    "flow",
    "history",
    "show",
    "symbol",
    "version",
    "versions",
}

DECISION_HISTORY_OPERATOR_TERMS = {
    "decision",
    "decide",
    "decided",
    "made",
}

CODE_WHY_OPERATOR_TERMS = {
    "change",
    "changed",
    "code",
    "file",
}

AGENT_CONTEXT_TERMS = {
    "agent",
    "claude",
    "codex",
}


def rerank_document(
    *,
    query: str,
    intent: str,
    doc: RetrievalDocument,
    fused_score: float,
    neighbors: tuple[dict[str, Any], ...],
    source_scores: dict[str, float],
    bi_encoder_weight: float,
) -> tuple[float, list[str]]:
    terms = _terms(query)
    scoring_terms = terms
    if intent == "version_flow":
        # "version flow" is an operator phrase. Rank by the requested symbol/path
        # terms, otherwise functions named "version_flow" beat the actual symbol.
        scoring_terms = terms.difference(VERSION_FLOW_OPERATOR_TERMS) or terms
    text = _normalize(f"{doc.title} {doc.body} {json.dumps(doc.metadata, sort_keys=True)}")
    primary_text = _primary_rank_text(doc, include_code_locator_context=_query_has_code_locator(query))
    reasons = [f"fused:{round(fused_score, 6)}"]
    score = fused_score
    overlap = [term for term in scoring_terms if term in text]
    if overlap:
        overlap_ratio = len(overlap) / max(1, len(scoring_terms))
        score += min(0.4, overlap_ratio * 0.4)
        reasons.append("term_overlap:" + ",".join(overlap[:8]))
    topic_terms = _topic_terms(query, intent)
    topic_overlap_ratio = 0.0
    if topic_terms:
        topic_overlap = [term for term in topic_terms if term in primary_text]
        if topic_overlap:
            topic_overlap_ratio = len(topic_overlap) / max(1, len(topic_terms))
            score += min(0.5, topic_overlap_ratio * 0.5)
            reasons.append("topic_focus_overlap:" + ",".join(topic_overlap[:8]))
        elif intent in {"code_why", "decision_history"}:
            score -= 0.18
            reasons.append("topic_focus_penalty")
    if doc.doc_type == "central_version":
        central_boost = _central_version_boost(doc, intent=intent, query=query, topic_overlap_ratio=topic_overlap_ratio)
        score += central_boost
        if central_boost:
            reasons.append(f"central_active_boost:{round(central_boost, 3)}")
        else:
            score -= 0.05
            reasons.append("central_low_topic_overlap_penalty")
        if intent == "version_flow" and _query_has_code_locator(query) and not _code_locator_match(query, text):
            score -= 0.25
            reasons.append("code_locator_mismatch_penalty")
    elif doc.doc_type == "central_atom":
        if topic_overlap_ratio >= 0.4 or intent == "version_flow" or _query_has_code_locator(query):
            score += 0.10
            reasons.append("central_atom_context_boost")
    elif doc.doc_type == "graph_lineage" and intent not in {"version_flow"}:
        score -= 0.12
        reasons.append("graph_lineage_penalty")
    if intent in {"code_why", "decision_history"} and doc.doc_type == "reasoning":
        score += 0.25
        reasons.append("reasoning_boost")
    if intent == "code_why" and doc.doc_type == "code_impact":
        score += 0.24
        reasons.append("code_impact_boost")
        if _code_locator_match(query, text):
            score += 0.12
            reasons.append("code_locator_impact_boost")
    if intent == "code_why" and doc.doc_type == "file_impact":
        score += 0.32
        reasons.append("file_impact_boost")
        if _code_locator_match(query, text):
            score += 0.18
            reasons.append("code_locator_file_rollup_boost")
        elif _query_has_code_locator(query):
            score -= 0.28
            reasons.append("code_locator_mismatch_penalty")
    code_locator_query = _query_has_code_locator(query)
    strict_locator_query = bool(_strict_code_locator_terms(query))
    strict_locator_match = _strict_code_locator_match(query, text)
    if strict_locator_query:
        if strict_locator_match:
            score += 0.22
            reasons.append("strict_code_locator_match")
        else:
            score -= 0.85
            reasons.append("strict_code_locator_mismatch")
    if intent == "version_flow" and doc.doc_type == "file_impact":
        # FileImpactSummary is the curated per-file rollup that carries the
        # ordered commit/reason packet context. A central file KnowledgeVersion
        # only says "this file exists in active memory"; it is not enough to
        # explain evolution by itself.
        score += 0.36
        reasons.append("version_file_impact_boost")
        if _code_locator_match(query, text):
            score += 0.24
            reasons.append("version_locator_file_rollup_boost")
        elif code_locator_query:
            score -= 0.50
            reasons.append("code_locator_mismatch_penalty")
    if intent == "version_flow" and doc.doc_type == "code_impact":
        score += 0.26
        reasons.append("version_code_impact_boost")
        if _code_locator_match(query, text):
            score += 0.18
            reasons.append("version_locator_code_impact_boost")
        elif code_locator_query:
            score -= 0.40
            reasons.append("code_locator_mismatch_penalty")
    if intent == "version_flow" and doc.doc_type in {"packet", "reasoning"} and code_locator_query:
        if _code_locator_match(query, text):
            score += 0.14
            reasons.append("version_locator_reasoning_context_boost")
        else:
            score -= 0.20
            reasons.append("code_locator_mismatch_penalty")
    if intent in {"code_why", "version_flow"} and doc.doc_type in {"file_ref", "symbol_ref", "code_region_ref"}:
        if code_locator_query:
            score += 0.08
            reasons.append("curated_code_support_boost")
        else:
            score -= 0.14
            reasons.append("broad_query_code_support_penalty")
    if intent == "code_why" and doc.doc_type == "packet":
        # Packets often contain the user's original question verbatim. Keep
        # them as trace support, but do not let query echo beat impact docs.
        score -= 0.25
        reasons.append("packet_support_penalty")
    node_type = _doc_node_type(doc)
    if intent == "decision_history" and doc.doc_type == "reasoning":
        if node_type == "Decision":
            score += 0.18
            reasons.append("decision_node_boost")
        elif node_type in {"Cause", "Fix", "Constraint"}:
            score += 0.08
            reasons.append("decision_context_boost")
    if "vector" in source_scores:
        vector_score = max(0.0, min(1.0, float(source_scores["vector"])))
        vector_boost = vector_score * max(0.0, float(bi_encoder_weight))
        score += vector_boost
        reasons.append(f"bi_encoder_score:{round(vector_score, 6)}")
        reasons.append(f"bi_encoder_boost:{round(vector_boost, 6)}")
    if intent == "version_flow" and doc.doc_type in {"symbol", "code"}:
        score += 0.25
        reasons.append("version_flow_boost")
        if overlap:
            target_ratio = len(overlap) / max(1, len(scoring_terms))
            score += target_ratio * 0.2
            reasons.append(f"version_target_overlap:{round(target_ratio, 3)}")
        if doc.doc_type == "symbol":
            score += 0.1
            reasons.append("symbol_version_boost")
    if doc.memory_class == "supporting_evidence":
        score -= 0.18
        reasons.append("supporting_evidence_penalty")
        if intent in {"code_why", "decision_history"}:
            score -= 0.10
            reasons.append("answer_query_evidence_penalty")
    if doc.doc_type == "commit":
        score -= 0.12
        reasons.append("commit_hub_penalty")
    if _looks_like_test_artifact(doc) and "test" not in terms:
        score -= 0.08
        reasons.append("test_artifact_penalty")
    role = _doc_impact_role(doc)
    if role == "validation_test" and "test" not in terms:
        penalty = 0.45 if doc.doc_type in {"symbol_ref", "code_region_ref", "file_ref"} else 0.18
        score -= penalty
        reasons.append(f"validation_support_penalty:{round(penalty, 2)}")
    elif role in {"docs", "config"} and not code_locator_query:
        score -= 0.04
        reasons.append(f"{role}_support_penalty")
    neighbor_text = _normalize(" ".join(f"{n.get('label') or ''} {n.get('summary') or ''}" for n in neighbors))
    if neighbor_text and any(term in neighbor_text for term in terms):
        score += 0.08
        reasons.append("neighbor_overlap")
    score += min(max(doc.importance, 0.0), 1.0) * 0.05
    return score, reasons


def _doc_impact_role(doc: RetrievalDocument) -> str:
    metadata = doc.metadata if isinstance(doc.metadata, dict) else {}
    node_metadata = metadata.get("node_metadata") if isinstance(metadata.get("node_metadata"), dict) else {}
    return str(metadata.get("impact_role") or metadata.get("primary_impact_role") or node_metadata.get("impact_role") or node_metadata.get("primary_impact_role") or "")


def _topic_terms(query: str, intent: str) -> set[str]:
    terms = set(_expanded_query_terms(query))
    if "hook" in terms:
        # In AMO queries, "Codex hooks" usually names the agent surface.
        # The durable topic is the hook behavior: capture, injection, prompt flow.
        terms = terms.difference(AGENT_CONTEXT_TERMS)
    if intent == "decision_history":
        return terms.difference(DECISION_HISTORY_OPERATOR_TERMS)
    if intent == "code_why":
        return terms.difference(CODE_WHY_OPERATOR_TERMS)
    if intent == "version_flow":
        return terms.difference(VERSION_FLOW_OPERATOR_TERMS)
    return terms


def _code_locator_terms(query: str) -> set[str]:
    terms: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9_./:-]+", str(query or "")):
        lowered = token.lower().replace("\\", "/")
        if not lowered:
            continue
        if "_" in lowered or "::" in lowered or "/" in lowered or "." in lowered:
            terms.add(lowered)
            parts = [part for part in re.split(r"[^a-zA-Z0-9_]+", lowered) if len(part) > 2]
            terms.update(_stem_term(part) for part in parts if part not in QUERY_STOPWORDS)
    return terms


def _code_locator_match(query: str, normalized_doc_text: str) -> bool:
    if _strict_code_locator_terms(query):
        return _strict_code_locator_match(query, normalized_doc_text)
    locator_terms = _code_locator_terms(query)
    if not locator_terms:
        return False
    text = normalized_doc_text.lower()
    return any(term in text for term in locator_terms)


def _strict_code_locator_terms(query: str) -> set[str]:
    out: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9_./:-]+", str(query or "")):
        lowered = token.lower().replace("\\", "/").strip(".,;:()[]{}")
        if not lowered:
            continue
        if "/" in lowered or "." in lowered or "::" in lowered:
            out.add(lowered)
    return out


def _strict_code_locator_match(query: str, normalized_doc_text: str) -> bool:
    locators = _strict_code_locator_terms(query)
    if not locators:
        return False
    text = normalized_doc_text.lower().replace("\\", "/")
    for locator in locators:
        if locator in text:
            return True
        if "::" in locator:
            path_part, symbol_part = locator.split("::", 1)
            path_stem = path_part.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            if symbol_part and symbol_part in text and (path_part in text or (path_stem and path_stem in text)):
                return True
    return False


def _primary_rank_text(doc: RetrievalDocument, *, include_code_locator_context: bool = False) -> str:
    if doc.doc_type != "reasoning":
        return _normalize(f"{doc.title} {doc.body}")

    kept: list[str] = [doc.title]
    for raw_line in doc.body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        prefix = line.split(":", 1)[0].strip().lower()
        if prefix in {"changed paths", "linked code", "evidence", "metadata", "paths", "file_path", "symbol"} and not include_code_locator_context:
            continue
        kept.append(line)
    return _normalize(" ".join(kept))


def _doc_node_type(doc: RetrievalDocument) -> str:
    metadata = doc.metadata.get("node_metadata") if isinstance(doc.metadata, dict) else None
    if isinstance(metadata, dict):
        return str(metadata.get("node_type") or "")
    return str(doc.metadata.get("node_type") or "") if isinstance(doc.metadata, dict) else ""


def _central_version_boost(
    doc: RetrievalDocument,
    *,
    intent: str,
    query: str,
    topic_overlap_ratio: float,
) -> float:
    metadata = doc.metadata.get("node_metadata") if isinstance(doc.metadata, dict) else {}
    atom_kind = str(metadata.get("atom_kind") or "") if isinstance(metadata, dict) else ""
    if atom_kind in {"decision", "problem"}:
        if intent == "decision_history":
            if topic_overlap_ratio >= 0.6:
                return 0.85
            if topic_overlap_ratio >= 0.4:
                return 0.45
            return 0.0
        if intent == "code_why" or _query_has_code_locator(query):
            if topic_overlap_ratio >= 0.75:
                return 0.35
            if topic_overlap_ratio >= 0.6:
                return 0.20
            return 0.0
        if topic_overlap_ratio >= 0.6:
            return 0.25
        return 0.0
    if intent == "version_flow":
        # File/commit KnowledgeVersions are identity stubs. Keep them visible,
        # but let curated FileImpact/CodeImpact docs explain the actual
        # evolution when they are available.
        if atom_kind in {"file", "commit"}:
            return 0.25
        return 0.55
    if intent == "code_why" and atom_kind in {"file", "commit"}:
        # File/commit KnowledgeVersions are identity stubs. A "why" query
        # needs curated FileImpact/CodeImpact or reasoning docs when available.
        if _query_has_code_locator(query) and topic_overlap_ratio >= 0.4:
            return 0.12
        return 0.0
    if _query_has_code_locator(query):
        return 0.55
    if atom_kind in {"file", "commit"} and intent == "semantic_search":
        if topic_overlap_ratio >= 0.6:
            return 0.18
        if topic_overlap_ratio >= 0.4:
            return 0.08
        return 0.0
    if topic_overlap_ratio >= 0.6:
        return 0.65
    if topic_overlap_ratio >= 0.4:
        return 0.55
    return 0.0


def _looks_like_test_artifact(doc: RetrievalDocument) -> bool:
    lowered = f"{doc.title} {doc.body}".lower()
    return "tests/" in lowered or "tests\\" in lowered or "test_" in lowered


__all__ = [
    "AGENT_CONTEXT_TERMS",
    "CODE_WHY_OPERATOR_TERMS",
    "DECISION_HISTORY_OPERATOR_TERMS",
    "VERSION_FLOW_OPERATOR_TERMS",
    "rerank_document",
]
