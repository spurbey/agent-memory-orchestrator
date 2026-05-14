from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "and",
    "are",
    "because",
    "been",
    "before",
    "being",
    "between",
    "but",
    "can",
    "did",
    "does",
    "for",
    "from",
    "has",
    "have",
    "how",
    "into",
    "its",
    "not",
    "now",
    "only",
    "should",
    "that",
    "the",
    "then",
    "this",
    "use",
    "used",
    "using",
    "via",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "why",
    "will",
    "with",
    "would",
}


class GraphSearchCache:
    """Rebuildable lexical retrieval cache derived from graph nodes.

    Kuzu remains graph truth. This cache is a local acceleration layer and can
    be deleted/rebuilt without losing memory.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def rebuild(self, nodes: list[dict[str, Any]]) -> dict[str, Any]:
        docs: list[dict[str, Any]] = []
        df: dict[str, int] = {}
        for node in nodes:
            tokens = _terms(_node_text(node))
            if not tokens:
                continue
            counts: dict[str, int] = {}
            for token in tokens:
                counts[token] = counts.get(token, 0) + 1
            for token in counts:
                df[token] = df.get(token, 0) + 1
            docs.append(
                {
                    "id": str(node.get("id") or ""),
                    "kind": str(node.get("kind") or ""),
                    "node": _cache_node(node),
                    "tokens": counts,
                    "length": sum(counts.values()),
                }
            )

        avgdl = sum(int(doc["length"]) for doc in docs) / len(docs) if docs else 0.0
        payload = {
            "version": 1,
            "built_at": datetime.now(timezone.utc).isoformat(),
            "doc_count": len(docs),
            "avgdl": avgdl,
            "df": df,
            "docs": docs,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_name(f"{self.path.name}.tmp")
        tmp_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)
        return {
            "ok": True,
            "cache_path": str(self.path),
            "doc_count": len(docs),
            "term_count": len(df),
            "built_at": payload["built_at"],
        }

    def status(self) -> dict[str, Any]:
        payload = self._load()
        if payload is None:
            return {"ok": True, "exists": False, "cache_path": str(self.path)}
        return {
            "ok": True,
            "exists": True,
            "cache_path": str(self.path),
            "built_at": payload.get("built_at", ""),
            "doc_count": int(payload.get("doc_count") or 0),
            "term_count": len(payload.get("df") or {}),
        }

    def search(self, query: str, *, limit: int = 25, kinds: list[str] | None = None) -> list[dict[str, Any]]:
        payload = self._load()
        if payload is None:
            return []
        query_terms = _terms(query)
        if not query_terms:
            return []
        allowed = set(kinds or [])
        docs = payload.get("docs") if isinstance(payload.get("docs"), list) else []
        df = payload.get("df") if isinstance(payload.get("df"), dict) else {}
        doc_count = max(1, int(payload.get("doc_count") or len(docs) or 1))
        avgdl = float(payload.get("avgdl") or 1.0)
        rows: list[tuple[float, dict[str, Any]]] = []
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            node = doc.get("node")
            if not isinstance(node, dict):
                continue
            if allowed and str(node.get("kind") or "") not in allowed:
                continue
            score = _bm25(query_terms, doc, df=df, doc_count=doc_count, avgdl=avgdl)
            if score <= 0:
                continue
            status_bonus = 0.3 if node.get("status") in {"active", "committed"} else 0.0
            evidence_bonus = 0.15 if node.get("evidence_id") else 0.0
            total = score + status_bonus + evidence_bonus
            rows.append((total, {**node, "graph_score": round(total, 6), "cache_hit": True}))
        rows.sort(key=lambda item: item[0], reverse=True)
        return [node for _score, node in rows[: max(1, int(limit))]]

    def _load(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) and payload.get("version") == 1 else None


def _cache_node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node.get("id", ""),
        "kind": node.get("kind", ""),
        "label": node.get("label", ""),
        "summary": node.get("summary", ""),
        "status": node.get("status", ""),
        "scope": node.get("scope", ""),
        "session_id": node.get("session_id", ""),
        "project_id": node.get("project_id", ""),
        "source_app": node.get("source_app", ""),
        "evidence_id": node.get("evidence_id", ""),
        "commit_id": node.get("commit_id", ""),
        "created_at": node.get("created_at", ""),
        "updated_at": node.get("updated_at", ""),
        "metadata": node.get("metadata") if isinstance(node.get("metadata"), dict) else {},
    }


def _node_text(node: dict[str, Any]) -> str:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    changed_files = metadata.get("changed_files") if isinstance(metadata.get("changed_files"), list) else []
    tests = metadata.get("tests") if isinstance(metadata.get("tests"), list) else []
    parts = [
        node.get("kind"),
        node.get("label"),
        node.get("summary"),
        metadata.get("goal"),
        metadata.get("latest_decision"),
        metadata.get("next_step"),
        " ".join(str(item) for item in changed_files),
        " ".join(str(item) for item in tests),
    ]
    return " ".join(str(part or "") for part in parts)


def _terms(text: str) -> list[str]:
    terms: list[str] = []
    for token in re.findall(r"[a-z0-9_.-]+", str(text or "").lower()):
        if len(token) <= 2:
            continue
        if token in STOPWORDS:
            continue
        if re.fullmatch(r"[0-9a-f]{16,40}", token):
            continue
        terms.append(token)
    return terms


def _bm25(query_terms: list[str], doc: dict[str, Any], *, df: dict[str, Any], doc_count: int, avgdl: float) -> float:
    counts = doc.get("tokens") if isinstance(doc.get("tokens"), dict) else {}
    doc_len = max(1, int(doc.get("length") or 1))
    k1 = 1.2
    b = 0.75
    score = 0.0
    for term in query_terms:
        tf = int(counts.get(term) or 0)
        if tf <= 0:
            continue
        term_df = max(0, int(df.get(term) or 0))
        idf = math.log(1 + (doc_count - term_df + 0.5) / (term_df + 0.5))
        denom = tf + k1 * (1 - b + b * (doc_len / max(avgdl, 1.0)))
        score += idf * ((tf * (k1 + 1)) / denom)
    return score
