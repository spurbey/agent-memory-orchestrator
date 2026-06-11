"""Semantic Harness domain primitives.

Phase 1 is intentionally structural-only: deterministic IDs, repo graph
bootstrap, exact anchor resolution, and strict cards. Historical AMO imports,
Qwen enrichment, vectors, and sidecar injection layer on top of this boundary.
"""

from .anchor_resolution import ResolvedAnchors
from .anchor_resolution import resolve_anchors
from .bootstrap import build_structural_graph
from .commit_update import CommitWorkWindow
from .commit_update import GraphUpdateDelta
from .commit_update import build_commit_update_delta
from .git_diff import parse_unified_diff_hunks
from .hunk_mapping import CommitHunk
from .hunk_mapping import HunkEntityMapping
from .hunk_mapping import HunkRange
from .hunk_mapping import map_hunk_to_entities
from .identity import code_region_id
from .identity import commit_id
from .identity import file_id
from .identity import harness_card_id
from .identity import hunk_id
from .identity import normalize_file_path
from .identity import repo_id_for_root
from .identity import symbol_id
from .identity import version_id
from .identity import work_window_id
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
    "CommitHunk",
    "CommitWorkWindow",
    "GraphUpdateDelta",
    "HunkEntityMapping",
    "HunkRange",
    "ResolvedAnchors",
    "SourceFile",
    "StructuralHarnessGraph",
    "answer_structural_query",
    "build_structural_graph",
    "build_commit_update_delta",
    "code_region_id",
    "commit_id",
    "file_id",
    "harness_card_id",
    "hunk_id",
    "map_hunk_to_entities",
    "normalize_file_path",
    "parse_unified_diff_hunks",
    "repo_id_for_root",
    "resolve_anchors",
    "symbol_id",
    "version_id",
    "work_window_id",
]
