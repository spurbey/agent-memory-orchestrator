from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from .graph_writer import CompactSessionGraph


TRACE_ONLY_KINDS = {
    "assert",
    "expr",
    "for",
    "if",
    "import",
    "import_block",
    "return",
    "try",
    "with",
}

SUPPORT_KINDS = {
    "class",
    "config_key",
    "doc_section",
    "function",
    "markup_element",
    "module_assignment_block",
    "style_rule",
}

CENTRAL_CODE_ROLES = frozenset({"primary_implementation"})

STOPWORDS = {
    "about",
    "after",
    "and",
    "are",
    "because",
    "code",
    "commit",
    "file",
    "files",
    "for",
    "from",
    "graph",
    "how",
    "implement",
    "implementation",
    "into",
    "local",
    "make",
    "memory",
    "repo",
    "run",
    "session",
    "stage",
    "system",
    "that",
    "the",
    "this",
    "use",
    "used",
    "using",
    "v2",
    "what",
    "when",
    "where",
    "why",
    "with",
}


@dataclass(slots=True, frozen=True)
class CuratedGraphBuild:
    graph: CompactSessionGraph
    audit: dict[str, Any]


def build_curated_session_graph(
    *,
    packets: list[dict[str, Any]],
    reasoning_nodes: list[dict[str, Any]],
    evidence_refs: list[dict[str, Any]],
    commit_nodes: list[dict[str, Any]],
    code_hunks: list[dict[str, Any]],
    code_nodes: list[dict[str, Any]],
    max_files_per_packet: int = 10,
    max_code_refs_per_packet: int = 16,
) -> CuratedGraphBuild:
    """Build the answer-grade/support-grade graph used by central merge.

    The full trace graph still owns raw hunks, raw AST regions, and exhaustive
    symbol/version edges. This graph keeps only the material that is useful for
    central memory and retrieval.
    """

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    edge_ids: set[tuple[str, str, str]] = set()

    def add_node(node: dict[str, Any]) -> None:
        node_id = str(node.get("id") or "")
        if not node_id or node_id in node_ids:
            return
        node_ids.add(node_id)
        nodes.append(node)

    def add_edge(from_id: str, to_id: str, kind: str, properties: dict[str, Any] | None = None) -> None:
        if not from_id or not to_id or from_id not in node_ids or to_id not in node_ids:
            return
        key = (from_id, to_id, kind)
        if key in edge_ids:
            return
        edge_ids.add(key)
        edges.append(
            {
                "from_id": from_id,
                "to_id": to_id,
                "kind": kind,
                "properties_json": json.dumps(properties or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            }
        )

    packet_by_id = {str(packet.get("packet_id") or ""): packet for packet in packets}
    reasoning_by_packet: dict[str, list[dict[str, Any]]] = defaultdict(list)
    code_by_packet: dict[str, list[dict[str, Any]]] = defaultdict(list)
    hunks_by_packet: dict[str, list[dict[str, Any]]] = defaultdict(list)
    evidence_by_id = {str(item.get("evidence_ref_id") or item.get("ref") or ""): item for item in evidence_refs}
    commit_by_id = {str(item.get("packet_id") or ""): item for item in commit_nodes}

    for item in reasoning_nodes:
        reasoning_by_packet[str(item.get("source_packet_id") or "")].append(item)
    for item in code_nodes:
        code_by_packet[str(item.get("packet_id") or "")].append(item)
    for item in code_hunks:
        hunks_by_packet[str(item.get("packet_id") or "")].append(item)

    cited_evidence = {
        str(ref)
        for item in reasoning_nodes
        for ref in (item.get("evidence_refs", []) if isinstance(item.get("evidence_refs"), list) else [])
        if str(ref)
    }

    for packet in packets:
        packet_id = str(packet.get("packet_id") or "")
        commit = packet.get("commit") if isinstance(packet.get("commit"), dict) else {}
        commit_sha = str(commit.get("short_sha") or "")
        add_node(
            _node(
                node_id=packet_id,
                kind="Packet",
                packet_id=packet_id,
                commit_sha=commit_sha,
                label=f"{packet_id} {commit.get('message') or ''}".strip(),
                summary="; ".join(_ref_excerpt(ref) for ref in packet.get("problem_refs", [])[:2] if isinstance(ref, dict)),
                properties=packet,
            )
        )
        commit_item = commit_by_id.get(packet_id) or {**commit, "packet_id": packet_id}
        add_node(
            _node(
                node_id=str(commit_item.get("commit_node_id") or f"commit:{commit_sha}"),
                kind="Commit",
                packet_id=packet_id,
                commit_sha=commit_sha,
                label=f"{commit_sha} {commit.get('message') or ''}".strip(),
                summary=f"Changed {commit.get('changed_files_count') or 0} files",
                properties=commit_item,
            )
        )

    for item in reasoning_nodes:
        node_id = str(item.get("reasoning_node_id") or item.get("node_id") or "")
        packet_id = str(item.get("source_packet_id") or "")
        add_node(
            _node(
                node_id=node_id,
                kind="ReasoningNode",
                packet_id=packet_id,
                commit_sha=str(item.get("source_commit_sha") or ""),
                label=f"{item.get('node_type') or ''}: {item.get('subject') or ''}".strip(),
                summary=str(item.get("statement") or ""),
                properties={**item, "promotion_grade": "answer_grade"},
            )
        )

    for evidence_id in sorted(cited_evidence):
        item = evidence_by_id.get(evidence_id)
        if not item:
            continue
        add_node(
            _node(
                node_id=evidence_id,
                kind="EvidenceRef",
                packet_id=str(item.get("packet_id") or ""),
                commit_sha=str(item.get("commit_sha") or ""),
                label=evidence_id,
                summary=str(item.get("excerpt") or item.get("command") or ""),
                properties={**item, "promotion_grade": "support_grade"},
            )
        )

    policy_counts: Counter[str] = Counter()
    packet_audit: list[dict[str, Any]] = []
    selected_file_refs: dict[str, dict[str, Any]] = {}
    selected_symbol_refs: dict[str, dict[str, Any]] = {}
    selected_code_refs: dict[str, dict[str, Any]] = {}
    file_impacts: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for packet_id, packet in packet_by_id.items():
        commit = packet.get("commit") if isinstance(packet.get("commit"), dict) else {}
        commit_sha = str(commit.get("short_sha") or "")
        reasoning_text = " ".join(
            str(item.get(key) or "")
            for item in reasoning_by_packet.get(packet_id, [])
            for key in ("subject", "statement", "reason")
        )
        query_terms = _terms(f"{reasoning_text} {commit.get('message') or ''}")
        scored = _score_code_candidates(
            code_by_packet.get(packet_id, []),
            query_terms=query_terms,
            policy_counts=policy_counts,
        )
        selected_paths = _select_paths(scored, max_files=max_files_per_packet)
        selected_regions = _select_code_regions(scored, selected_paths=selected_paths, max_regions=max_code_refs_per_packet)

        impact_id = f"impact:{packet_id}:{commit_sha}"
        symbol_ids: list[str] = []
        region_ids: list[str] = []
        for selected in selected_regions:
            code_node = selected["node"]
            path = _norm_path(code_node.get("path"))
            qualified_name = str(code_node.get("qualified_name") or "")
            symbol_kind = str(code_node.get("symbol_kind") or "")
            impact_role = _impact_role(path=path, symbol_kind=symbol_kind)
            central_atom_candidate = _central_atom_candidate(selected, impact_role=impact_role)
            file_id = f"file:{_hash(path)}"
            symbol_id = f"symref:{_hash('|'.join([path, qualified_name]))}"
            region_key = "|".join([path, qualified_name, str(code_node.get("line_start") or ""), str(code_node.get("line_end") or "")])
            region_id = f"coderef:{_hash(region_key)}"
            selected_file_refs[file_id] = {
                "path": path,
                "packet_id": packet_id,
                "commit_sha": commit_sha,
                "impact_role": impact_role,
                "score": selected["file_score"],
                "reasons": selected["file_reasons"],
            }
            selected_symbol_refs[symbol_id] = {
                "path": path,
                "qualified_name": qualified_name,
                "symbol_kind": symbol_kind,
                "packet_id": packet_id,
                "commit_sha": commit_sha,
                "impact_role": impact_role,
                "score": selected["score"],
                "reasons": selected["reasons"],
                "central_atom_candidate": central_atom_candidate,
            }
            selected_code_refs[region_id] = {
                "path": path,
                "qualified_name": qualified_name,
                "symbol_kind": symbol_kind,
                "packet_id": packet_id,
                "commit_sha": commit_sha,
                "original_code_node_id": str(code_node.get("code_node_id") or ""),
                "impact_role": impact_role,
                "score": selected["score"],
                "reasons": selected["reasons"],
                "central_atom_candidate": central_atom_candidate,
            }
            symbol_ids.append(symbol_id)
            region_ids.append(region_id)

        reasoning_statements = [
            str(item.get("statement") or "")
            for item in reasoning_by_packet.get(packet_id, [])
            if str(item.get("statement") or "").strip()
        ]
        selected_file_list = list(selected_paths)
        file_score_lookup = dict(selected_paths.file_scores) if isinstance(selected_paths, SelectedPaths) else {}
        file_role_lookup: dict[str, str] = {}
        for path in selected_file_list:
            file_id = f"file:{_hash(path)}"
            impact_role = _impact_role(path=path, symbol_kind="")
            file_role_lookup[path] = impact_role
            selected_file_refs.setdefault(
                file_id,
                {
                    "path": path,
                    "packet_id": packet_id,
                    "commit_sha": commit_sha,
                    "impact_role": impact_role,
                    "score": file_score_lookup.get(path, 0),
                    "reasons": ["selected_file"],
                },
            )
            file_impacts[path].append(
                {
                    "packet_id": packet_id,
                    "commit_sha": commit_sha,
                    "commit_message": str(commit.get("message") or ""),
                    "impact_id": impact_id,
                    "impact_role": impact_role,
                    "reason": reasoning_statements[0] if reasoning_statements else "",
                }
            )
        impact_summary = " ".join(
            part
            for part in (
                f"Commit {commit.get('message') or ''}".strip(),
                f"Reason: {reasoning_statements[0]}" if reasoning_statements else "",
                f"Selected files: {', '.join(selected_file_list[:8])}" if selected_file_list else "",
            )
            if part
        )
        add_node(
            _node(
                node_id=impact_id,
                kind="CodeImpactSummary",
                packet_id=packet_id,
                commit_sha=commit_sha,
                label=f"Code impact for {packet_id}",
                summary=impact_summary,
                properties={
                    "packet_id": packet_id,
                    "commit_sha": commit_sha,
                    "changed_file_count": commit.get("changed_files_count"),
                    "hunk_count": len(hunks_by_packet.get(packet_id, [])),
                    "selected_files": selected_file_list,
                    "selected_file_roles": file_role_lookup,
                    "impact_roles": sorted(set(file_role_lookup.values())),
                    "selected_symbol_refs": list(dict.fromkeys(symbol_ids)),
                    "selected_code_refs": list(dict.fromkeys(region_ids)),
                    "reasoning_statements": reasoning_statements,
                    "promotion_grade": "support_grade",
                    "policy": "deterministic_v1",
                },
            )
        )
        packet_audit.append(
            {
                "packet_id": packet_id,
                "commit": commit.get("message") or "",
                "raw_code_node_count": len(code_by_packet.get(packet_id, [])),
                "raw_hunk_count": len(hunks_by_packet.get(packet_id, [])),
                "selected_files": list(selected_paths),
                "selected_file_roles": file_role_lookup,
                "selected_code_region_count": len(region_ids),
                "top_file_scores": selected_paths.file_scores[:8] if isinstance(selected_paths, SelectedPaths) else [],
            }
        )

    for file_id, item in selected_file_refs.items():
        add_node(
            _node(
                node_id=file_id,
                kind="FileRef",
                packet_id=str(item.get("packet_id") or ""),
                commit_sha=str(item.get("commit_sha") or ""),
                label=str(item["path"]),
                summary=f"Touched file {item['path']}",
                properties={**item, "promotion_grade": "support_grade"},
            )
        )
    for symbol_id, item in selected_symbol_refs.items():
        add_node(
            _node(
                node_id=symbol_id,
                kind="SymbolRef",
                packet_id=str(item.get("packet_id") or ""),
                commit_sha=str(item.get("commit_sha") or ""),
                label=f"{item['path']}::{item['qualified_name']}",
                summary=f"{item['symbol_kind']} {item['qualified_name']}".strip(),
                properties={**item, "promotion_grade": "support_grade"},
            )
        )
    for region_id, item in selected_code_refs.items():
        add_node(
            _node(
                node_id=region_id,
                kind="CodeRegionRef",
                packet_id=str(item.get("packet_id") or ""),
                commit_sha=str(item.get("commit_sha") or ""),
                label=f"{item['path']}::{item['qualified_name']}",
                summary=f"Representative {item['symbol_kind']} code region {item['qualified_name']}".strip(),
                properties={**item, "promotion_grade": "support_grade"},
            )
        )
    for path, impacts in sorted(file_impacts.items()):
        add_node(
            _node(
                node_id=f"fileimpact:{_hash(path)}",
                kind="FileImpactSummary",
                packet_id=str(impacts[0].get("packet_id") or "") if impacts else "",
                commit_sha=str(impacts[0].get("commit_sha") or "") if impacts else "",
                label=f"Impact summary for {path}",
                summary=_file_impact_summary(path, impacts),
                properties={
                    "path": path,
                    "impact_count": len(impacts),
                    "primary_impact_role": _primary_impact_role(impacts),
                    "impact_role_counts": dict(Counter(str(item.get("impact_role") or "support") for item in impacts)),
                    "packet_ids": [str(item.get("packet_id") or "") for item in impacts],
                    "commit_shas": [str(item.get("commit_sha") or "") for item in impacts],
                    "impact_ids": [str(item.get("impact_id") or "") for item in impacts],
                    "reasons": [str(item.get("reason") or "") for item in impacts if str(item.get("reason") or "").strip()][:8],
                    "commit_messages": [str(item.get("commit_message") or "") for item in impacts if str(item.get("commit_message") or "").strip()][:12],
                    "promotion_grade": "support_grade",
                    "policy": "deterministic_v1",
                },
            )
        )

    for packet in packets:
        packet_id = str(packet.get("packet_id") or "")
        commit = packet.get("commit") if isinstance(packet.get("commit"), dict) else {}
        commit_sha = str(commit.get("short_sha") or "")
        impact_id = f"impact:{packet_id}:{commit_sha}"
        add_edge(packet_id, impact_id, "PACKET_HAS_CODE_IMPACT")
        add_edge(impact_id, f"commit:{commit_sha}", "CODE_IMPACT_IMPLEMENTED_BY_COMMIT")

    for item in reasoning_nodes:
        node_id = str(item.get("reasoning_node_id") or item.get("node_id") or "")
        packet_id = str(item.get("source_packet_id") or "")
        commit_sha = str(item.get("source_commit_sha") or "")
        add_edge(node_id, packet_id, "REASON_NODE_IN_PACKET")
        add_edge(node_id, f"commit:{commit_sha}", "REASON_NODE_EXPLAINS_COMMIT")
        add_edge(node_id, f"impact:{packet_id}:{commit_sha}", "REASON_NODE_HAS_CODE_IMPACT")
        for evidence_id in item.get("evidence_refs", []) if isinstance(item.get("evidence_refs"), list) else []:
            add_edge(node_id, str(evidence_id), "REASON_NODE_EVIDENCED_BY")

    for impact_node in [node for node in nodes if node["kind"] == "CodeImpactSummary"]:
        impact = _properties(impact_node)
        impact_id = impact_node["id"]
        for path in impact.get("selected_files", []) if isinstance(impact.get("selected_files"), list) else []:
            normalized = _norm_path(path)
            file_id = f"file:{_hash(normalized)}"
            file_impact_id = f"fileimpact:{_hash(normalized)}"
            add_edge(impact_id, file_id, "CODE_IMPACT_TOUCHES_FILE")
            add_edge(file_impact_id, file_id, "FILE_IMPACT_FOR_FILE")
            add_edge(file_impact_id, impact_id, "FILE_IMPACT_INCLUDES_CODE_IMPACT")
        for symbol_id in impact.get("selected_symbol_refs", []) if isinstance(impact.get("selected_symbol_refs"), list) else []:
            add_edge(impact_id, str(symbol_id), "CODE_IMPACT_TOUCHES_SYMBOL")
        for region_id in impact.get("selected_code_refs", []) if isinstance(impact.get("selected_code_refs"), list) else []:
            add_edge(impact_id, str(region_id), "CODE_IMPACT_TOUCHES_CODE_REGION")

    inventory = {
        "manifest_node_count": len(nodes),
        "manifest_edge_count": len(edges),
        "unresolved_edge_count": 0,
        "node_kind_counts": dict(Counter(node["kind"] for node in nodes)),
        "edge_kind_counts": dict(Counter(edge["kind"] for edge in edges)),
    }
    audit = {
        "policy": "deterministic_v1",
        "policy_counts": dict(policy_counts),
        "selected_file_count": len(selected_file_refs),
        "file_impact_count": len(file_impacts),
        "selected_symbol_count": len(selected_symbol_refs),
        "selected_code_region_count": len(selected_code_refs),
        "packet_audit": packet_audit,
        "inventory": inventory,
    }
    return CuratedGraphBuild(
        graph=CompactSessionGraph(nodes=tuple(nodes), edges=tuple(edges), unresolved_edges=(), inventory=inventory),
        audit=audit,
    )


class SelectedPaths(tuple):
    file_scores: list[tuple[str, int]]

    def __new__(cls, values: list[str], file_scores: list[tuple[str, int]]) -> "SelectedPaths":
        obj = super().__new__(cls, values)
        obj.file_scores = file_scores
        return obj


def _score_code_candidates(
    code_nodes: list[dict[str, Any]],
    *,
    query_terms: set[str],
    policy_counts: Counter[str],
) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    file_scores: Counter[str] = Counter()
    file_reasons: dict[str, list[str]] = defaultdict(list)
    for code_node in code_nodes:
        path = _norm_path(code_node.get("path"))
        grade = _promotion_grade(code_node)
        policy_counts[grade] += 1
        code_terms = _terms(
            " ".join(
                [
                    path,
                    str(code_node.get("qualified_name") or ""),
                    str(code_node.get("symbol_kind") or ""),
                    str(code_node.get("text_excerpt") or "")[:500],
                ]
            )
        )
        overlap = len(query_terms.intersection(code_terms))
        score = 0
        reasons: list[str] = []
        if grade == "support_candidate":
            score += 5
            reasons.append("support_kind")
        elif grade == "trace_only":
            score -= 2
            reasons.append("trace_kind")
        else:
            score -= 4
            reasons.append("debug_kind")
        language = _language_for_path(path)
        if language == "python":
            score += 2
            reasons.append("python")
        elif language in {"javascript", "typescript", "dart", "markup", "css"}:
            score += 2
            reasons.append(language)
        elif language in {"config", "markdown"}:
            score += 1
            reasons.append(language)
        if path.startswith("tests/") or "/tests/" in path:
            score += 1
            reasons.append("test_support")
        if path.endswith(".md") and query_terms.intersection({"doc", "docs", "documentation", "readme"}):
            score += 3
            reasons.append("docs_intent")
        if overlap:
            score += min(6, overlap * 2)
            reasons.append(f"lexical:{overlap}")
        positive = max(0, score)
        file_scores[path] += positive
        file_reasons[path].extend(reasons)
        scored.append(
            {
                "node": code_node,
                "path": path,
                "score": score,
                "overlap": overlap,
                "grade": grade,
                "reasons": list(dict.fromkeys(reasons)),
                "file_scores": file_scores,
                "file_reasons": file_reasons,
            }
        )
    return scored


def _select_paths(scored: list[dict[str, Any]], *, max_files: int) -> SelectedPaths:
    if not scored:
        return SelectedPaths([], [])
    file_scores: Counter[str] = Counter()
    for item in scored:
        file_scores.update({item["path"]: max(0, int(item["score"]))})
    selected: list[str] = []
    for path, score in file_scores.most_common():
        if score >= 10 or (len(selected) < 3 and score > 0):
            selected.append(path)
        if len(selected) >= max(1, max_files):
            break
    return SelectedPaths(selected, file_scores.most_common())


def _select_code_regions(
    scored: list[dict[str, Any]],
    *,
    selected_paths: SelectedPaths,
    max_regions: int,
) -> list[dict[str, Any]]:
    selected_path_set = set(selected_paths)
    out: list[dict[str, Any]] = []
    role_counts: Counter[str] = Counter()
    for item in sorted(scored, key=lambda row: (row["score"], row["overlap"]), reverse=True):
        if item["path"] not in selected_path_set:
            continue
        if item["grade"] != "support_candidate":
            continue
        role = _impact_role(path=item["path"], symbol_kind=str(item["node"].get("symbol_kind") or ""))
        if role == "validation_test" and role_counts[role] >= 4:
            continue
        if role in {"docs", "config", "support"} and role_counts[role] >= 3:
            continue
        role_counts[role] += 1
        file_scores: Counter[str] = item["file_scores"]
        file_reasons: dict[str, list[str]] = item["file_reasons"]
        out.append(
            {
                **item,
                "file_score": file_scores[item["path"]],
                "file_reasons": list(dict.fromkeys(file_reasons[item["path"]]))[:8],
            }
        )
        if len(out) >= max(1, max_regions):
            break
    return out


def _promotion_grade(code_node: dict[str, Any]) -> str:
    kind = str(code_node.get("symbol_kind") or "").lower()
    path = _norm_path(code_node.get("path"))
    source = str(code_node.get("node_source") or "")
    qualified_name = str(code_node.get("qualified_name") or "")
    if kind in SUPPORT_KINDS and source == "parsed":
        return "support_candidate"
    if path.endswith(".py") and kind == "assignment" and qualified_name.isupper():
        return "support_candidate"
    if kind in TRACE_ONLY_KINDS or source.startswith("unparsed") or kind == "unparsed_hunk":
        return "trace_only"
    if path.endswith((".md", ".css", ".html", ".js", ".json", ".toml", ".example")):
        return "trace_only"
    return "debug_only"


def _central_atom_candidate(selected: dict[str, Any], *, impact_role: str) -> bool:
    node = selected.get("node") if isinstance(selected.get("node"), dict) else {}
    path = _norm_path(node.get("path"))
    if path.startswith("tests/") or "/tests/" in path:
        return False
    if impact_role not in CENTRAL_CODE_ROLES:
        return False
    kind = str(node.get("symbol_kind") or "").lower()
    if kind not in {"class", "function", "method", "config_key"}:
        return False
    return int(selected.get("score") or 0) >= 11


def _impact_role(*, path: str, symbol_kind: str) -> str:
    normalized = _norm_path(path)
    kind = str(symbol_kind or "").lower()
    if normalized.startswith("tests/") or "/tests/" in normalized or normalized.endswith(("_test.py", ".test.js", ".spec.js")):
        return "validation_test"
    if normalized.startswith("docs/") or "/docs/" in normalized or normalized.endswith((".md", ".mdx", ".rst")):
        return "docs"
    if normalized.endswith((".css", ".scss", ".sass", ".less")) or kind == "style_rule":
        return "ui_style"
    if normalized.endswith((".html", ".htm", ".xml", ".svg", ".vue", ".svelte")) or kind == "markup_element":
        return "ui_markup"
    if normalized.endswith((".json", ".jsonc", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env", ".example")) or kind == "config_key":
        return "config"
    if normalized.endswith((".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".dart", ".go", ".rs", ".java", ".kt", ".swift")):
        return "primary_implementation"
    return "support"


def _primary_impact_role(impacts: list[dict[str, Any]]) -> str:
    counts = Counter(str(item.get("impact_role") or "support") for item in impacts)
    for role in ("primary_implementation", "ui_style", "ui_markup", "config", "docs", "validation_test"):
        if counts.get(role):
            return role
    return "support"


def _language_for_path(path: str) -> str:
    if path.endswith(".py"):
        return "python"
    if path.endswith((".js", ".jsx", ".mjs", ".cjs")):
        return "javascript"
    if path.endswith((".ts", ".tsx")):
        return "typescript"
    if path.endswith(".dart"):
        return "dart"
    if path.endswith((".html", ".htm", ".xml", ".svg", ".vue", ".svelte")):
        return "markup"
    if path.endswith((".css", ".scss", ".sass", ".less")):
        return "css"
    if path.endswith((".json", ".jsonc", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env", ".example")):
        return "config"
    if path.endswith((".md", ".mdx")):
        return "markdown"
    return "unknown"


def _node(
    *,
    node_id: str,
    kind: str,
    packet_id: str,
    commit_sha: str,
    label: str,
    summary: str,
    properties: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": node_id,
        "kind": kind,
        "packet_id": packet_id,
        "commit_sha": commit_sha,
        "label": _clip(label, 300),
        "summary": _clip(summary, 1200),
        "properties_json": json.dumps(properties, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    }


def _properties(node: dict[str, Any]) -> dict[str, Any]:
    try:
        loaded = json.loads(str(node.get("properties_json") or "{}"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _terms(text: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", str(text).lower())
        if term not in STOPWORDS
    }


def _ref_excerpt(value: dict[str, Any]) -> str:
    return str(value.get("excerpt") or value.get("request") or value.get("command") or "")[:300]


def _file_impact_summary(path: str, impacts: list[dict[str, Any]]) -> str:
    commit_messages = [str(item.get("commit_message") or "").strip() for item in impacts if str(item.get("commit_message") or "").strip()]
    reasons = [str(item.get("reason") or "").strip() for item in impacts if str(item.get("reason") or "").strip()]
    parts = [f"{path} was touched by {len(impacts)} curated code impact(s)."]
    if commit_messages:
        parts.append("Commits: " + "; ".join(commit_messages[:8]))
    if reasons:
        parts.append("Reasons: " + " ".join(reasons[:3]))
    return _clip(" ".join(parts), 1200)


def _norm_path(value: object) -> str:
    return str(value or "").replace("\\", "/").lstrip("./")


def _hash(value: str, length: int = 20) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _clip(text: str, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 14)].rstrip() + " ... <clipped>"
