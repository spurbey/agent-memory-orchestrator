from __future__ import annotations

from ..domain.retrieval.constants import ANSWER_SEED_KINDS as ANSWER_SEED_KINDS
from ..domain.retrieval.constants import EVIDENCE_ONLY_KINDS as EVIDENCE_ONLY_KINDS
from ..domain.retrieval.constants import RETRIEVAL_STOPWORDS as RETRIEVAL_STOPWORDS
from ..domain.retrieval.constants import SUPPORT_ONLY_KINDS as SUPPORT_ONLY_KINDS

HOOK_CONTEXT_EVENTS = {"session_start"}
CAPTURE_ONLY_EVENTS = {"user_prompt_submit", "prompt", "post_tool_use", "tool_result", "stop", "session_stop"}
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

