from __future__ import annotations

from ..domain.retrieval.constants import ANSWER_SEED_KINDS as ANSWER_SEED_KINDS
from ..domain.retrieval.constants import EVIDENCE_ONLY_KINDS as EVIDENCE_ONLY_KINDS
from ..domain.retrieval.constants import RETRIEVAL_STOPWORDS as RETRIEVAL_STOPWORDS
from ..domain.retrieval.constants import SUPPORT_ONLY_KINDS as SUPPORT_ONLY_KINDS
from ..domain.versioning.flow import ISOLATED_GRAPH_VISUAL_KINDS as ISOLATED_GRAPH_VISUAL_KINDS
from ..domain.versioning.flow import ISOLATED_GRAPH_VISUAL_STATUSES as ISOLATED_GRAPH_VISUAL_STATUSES
from ..domain.versioning.flow import VERSION_FLOW_EDGE_KINDS as VERSION_FLOW_EDGE_KINDS
from ..domain.versioning.flow import VERSION_RELATION_EDGE_KINDS as VERSION_RELATION_EDGE_KINDS

HOOK_CONTEXT_EVENTS = {"session_start"}
CAPTURE_ONLY_EVENTS = {"user_prompt_submit", "prompt", "post_tool_use", "tool_result", "stop", "session_stop"}

