"""Semantic Harness domain primitives.

Phase 1 is intentionally structural-only: deterministic IDs, repo graph
bootstrap, exact anchor resolution, and strict cards. Historical AMO imports,
Qwen enrichment, vectors, and sidecar injection layer on top of this boundary.
"""

from .anchor_resolution import ResolvedAnchors
from .anchor_resolution import resolve_anchors
from .bootstrap import build_structural_graph
from .identity import code_region_id
from .identity import file_id
from .identity import harness_card_id
from .identity import normalize_file_path
from .identity import repo_id_for_root
from .identity import symbol_id
from .models import HarnessCard
from .models import HarnessEdge
from .models import HarnessNextAction
from .models import HarnessNode
from .models import HarnessQueryRequest
from .models import HarnessQueryResponse
from .models import SourceFile
from .models import StructuralHarnessGraph
from .query import answer_structural_query

__all__ = [
    "HarnessCard",
    "HarnessEdge",
    "HarnessNextAction",
    "HarnessNode",
    "HarnessQueryRequest",
    "HarnessQueryResponse",
    "ResolvedAnchors",
    "SourceFile",
    "StructuralHarnessGraph",
    "answer_structural_query",
    "build_structural_graph",
    "code_region_id",
    "file_id",
    "harness_card_id",
    "normalize_file_path",
    "repo_id_for_root",
    "resolve_anchors",
    "symbol_id",
]
