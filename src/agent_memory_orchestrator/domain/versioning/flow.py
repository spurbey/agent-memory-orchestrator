from __future__ import annotations

ISOLATED_GRAPH_VISUAL_KINDS = {
    "ReasoningNode",
    "DecisionUnit",
    "Problem",
    "Decision",
    "Cause",
    "Fix",
    "Constraint",
    "OpenQuestion",
    "WorkChange",
    "Commit",
    "GitCommit",
    "Packet",
    "CodeNode",
    "CodeVersion",
    "CodeHunk",
    "Symbol",
    "EvidenceRef",
}
ISOLATED_GRAPH_VISUAL_STATUSES = {
    "session_final",
    "candidate_reasoning_packet",
    "accepted",
    "active",
    "committed",
}
VERSION_FLOW_EDGE_KINDS = {
    "COMMITTED_AS",
    "REFINES",
    "SUPERSEDES",
    "DUPLICATE_OF",
    "CONTRADICTS",
    "VALIDATED_BY",
    "MODIFIES",
    "MERGED_INTO",
    "EVIDENCED_BY",
    "CLEANED_INTO",
    "EXTRACTED_AS",
    "CREATED",
    "PRODUCED",
    "HAS_WINDOW",
}
VERSION_RELATION_EDGE_KINDS = {"REFINES", "SUPERSEDES", "DUPLICATE_OF", "CONTRADICTS"}

__all__ = [
    "ISOLATED_GRAPH_VISUAL_KINDS",
    "ISOLATED_GRAPH_VISUAL_STATUSES",
    "VERSION_FLOW_EDGE_KINDS",
    "VERSION_RELATION_EDGE_KINDS",
]
