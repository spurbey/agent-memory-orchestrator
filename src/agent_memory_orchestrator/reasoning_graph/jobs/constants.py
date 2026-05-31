"""Compatibility wrapper for production pipeline constants.

New code should import from agent_memory_orchestrator.domain.pipeline.constants.
"""

from __future__ import annotations

from ...domain.pipeline.constants import CODE_PARSER_POLICY_VERSION
from ...domain.pipeline.constants import CENTRAL_MERGE_PLANNER_VERSION
from ...domain.pipeline.constants import CURATED_GRAPH_SCHEMA_VERSION
from ...domain.pipeline.constants import GRAPH_SCHEMA_VERSION
from ...domain.pipeline.constants import PIPELINE_VERSION
from ...domain.pipeline.constants import PRODUCTION_STAGES
from ...domain.pipeline.constants import PROMOTION_POLICY_VERSION
from ...domain.pipeline.constants import QUALITY_EVAL_POLICY_VERSION
from ...domain.pipeline.constants import REASONING_CODE_LINK_POLICY_VERSION
from ...domain.pipeline.constants import REASONING_REVIEW_POLICY_VERSION
from ...domain.pipeline.constants import RESET_MARKER_KEY
from ...domain.pipeline.constants import RETRIEVAL_PROJECTION_VERSION
from ...domain.pipeline.constants import SESSION_GRAPH_WRITER_VERSION
from ...domain.pipeline.constants import STAGE_DISPLAY_NAMES
from ...domain.pipeline.constants import SYMBOL_VERSION_POLICY_VERSION

__all__ = [
    "CODE_PARSER_POLICY_VERSION",
    "CENTRAL_MERGE_PLANNER_VERSION",
    "CURATED_GRAPH_SCHEMA_VERSION",
    "GRAPH_SCHEMA_VERSION",
    "PIPELINE_VERSION",
    "PRODUCTION_STAGES",
    "PROMOTION_POLICY_VERSION",
    "QUALITY_EVAL_POLICY_VERSION",
    "REASONING_CODE_LINK_POLICY_VERSION",
    "REASONING_REVIEW_POLICY_VERSION",
    "RESET_MARKER_KEY",
    "RETRIEVAL_PROJECTION_VERSION",
    "SESSION_GRAPH_WRITER_VERSION",
    "STAGE_DISPLAY_NAMES",
    "SYMBOL_VERSION_POLICY_VERSION",
]
