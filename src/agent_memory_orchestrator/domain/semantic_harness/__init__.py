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
from .doc_semantics import DocSemanticArtifact
from .doc_semantics import add_doc_semantics
from .git_diff import parse_unified_diff_hunks
from .hunk_mapping import CommitHunk
from .hunk_mapping import HunkEntityMapping
from .hunk_mapping import HunkRange
from .hunk_mapping import map_hunk_to_entities
from .identity import code_region_id
from .identity import commit_id
from .identity import doc_section_id
from .identity import docstring_id
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
from .projection import HarnessProjectionDocument
from .projection import HarnessProjectionSet
from .projection import DEFAULT_PROJECTION_VERSION
from .projection import build_projection_documents
from .projection import build_projection_set
from .projection import projection_set_id
from .query import answer_structural_query
from .relations import CoChangeSeed
from .relations import HistoricalRelationCandidate
from .relations import HistoricalRelationPolicy
from .relations import build_cochange_seed
from .relations import historical_relation_candidates
from .relations import should_show_historical_relation
from .retrieval import HASH_COSINE_METHOD
from .retrieval import HashVectorOptions
from .retrieval import LexicalRetrievalHit
from .retrieval import LexicalRetrievalOptions
from .retrieval import VectorRetrievalHit
from .retrieval import VectorRetrievalOptions
from .retrieval import hash_embed_text
from .retrieval import search_projection_documents
from .retrieval import search_projection_documents_vector
from .retrieval import tokenize_text
from .selection import CardSelectionOptions
from .selection import CardSelectionResult
from .selection import SuppressedCard
from .selection import card_selection_score
from .selection import select_harness_cards
from .snapshots import GRAPH_SCHEMA_VERSION
from .snapshots import GraphSnapshotIdentity
from .snapshots import graph_snapshot_id
from .snapshots import graph_snapshot_identity
from .store import EdgeKey
from .store import GraphDeltaApplyResult
from .store import HarnessGraphStore
from .store import InMemoryHarnessGraphStore
from .store import apply_graph_update_delta

__all__ = [
    "EdgeKey",
    "DEFAULT_PROJECTION_VERSION",
    "GraphDeltaApplyResult",
    "HASH_COSINE_METHOD",
    "HarnessCard",
    "HarnessEdge",
    "HarnessGraphStore",
    "HarnessNextAction",
    "HarnessNode",
    "HarnessProjectionDocument",
    "HarnessProjectionSet",
    "HarnessQueryRequest",
    "HarnessQueryResponse",
    "InMemoryHarnessGraphStore",
    "HashVectorOptions",
    "LexicalRetrievalHit",
    "LexicalRetrievalOptions",
    "VectorRetrievalHit",
    "VectorRetrievalOptions",
    "CommitHunk",
    "CommitWorkWindow",
    "CoChangeSeed",
    "CardSelectionOptions",
    "CardSelectionResult",
    "DocSemanticArtifact",
    "GraphUpdateDelta",
    "GRAPH_SCHEMA_VERSION",
    "GraphSnapshotIdentity",
    "HunkEntityMapping",
    "HunkRange",
    "HistoricalRelationCandidate",
    "HistoricalRelationPolicy",
    "ResolvedAnchors",
    "SourceFile",
    "StructuralHarnessGraph",
    "SuppressedCard",
    "answer_structural_query",
    "apply_graph_update_delta",
    "add_doc_semantics",
    "build_structural_graph",
    "build_commit_update_delta",
    "build_cochange_seed",
    "build_projection_documents",
    "build_projection_set",
    "historical_relation_candidates",
    "code_region_id",
    "commit_id",
    "doc_section_id",
    "docstring_id",
    "file_id",
    "harness_card_id",
    "hunk_id",
    "hash_embed_text",
    "card_selection_score",
    "graph_snapshot_id",
    "graph_snapshot_identity",
    "map_hunk_to_entities",
    "normalize_file_path",
    "parse_unified_diff_hunks",
    "projection_set_id",
    "repo_id_for_root",
    "resolve_anchors",
    "search_projection_documents",
    "search_projection_documents_vector",
    "select_harness_cards",
    "should_show_historical_relation",
    "symbol_id",
    "tokenize_text",
    "version_id",
    "work_window_id",
]
