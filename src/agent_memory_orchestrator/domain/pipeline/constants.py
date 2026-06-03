from __future__ import annotations

PIPELINE_VERSION = "production-2026-05"
GRAPH_SCHEMA_VERSION = "production-graph-v1"
REASONING_REVIEW_POLICY_VERSION = "reasoning-review-production-sha-equivalence"
CODE_PARSER_POLICY_VERSION = "code-parser-production-polyglot-guards"
SYMBOL_VERSION_POLICY_VERSION = "symbol-version-v1"
REASONING_CODE_LINK_POLICY_VERSION = "reasoning-code-link-v1"
PROMOTION_POLICY_VERSION = "curated-promotion-v1"
CURATED_GRAPH_SCHEMA_VERSION = "curated-session-graph-v1"
SESSION_GRAPH_WRITER_VERSION = "session-graph-writer-curated-v1"
CENTRAL_MERGE_PLANNER_VERSION = "central-version-merge-planner-v1"
RETRIEVAL_PROJECTION_VERSION = "curated-retrieval-projection-v1"
QUALITY_EVAL_POLICY_VERSION = "product-quality-eval-v1"

PRODUCTION_STAGES: tuple[str, ...] = (
    "evidence_view",
    "work_packets",
    "qwen_reasoning",
    "reasoning_review",
    "git_hunks",
    "ast_code_nodes",
    "symbol_versions",
    "reasoning_code_links",
    "kuzu_write",
    "central_version_merge",
    "retrieval_docs",
    "embeddings",
    "faiss",
    "quality_eval",
)

STAGE_DISPLAY_NAMES: dict[str, str] = {
    "evidence_view": "Evidence View",
    "work_packets": "Work Packets",
    "qwen_reasoning": "Qwen Reasoning",
    "reasoning_review": "Reasoning Review",
    "git_hunks": "Git Hunks",
    "ast_code_nodes": "AST Code Nodes",
    "symbol_versions": "Symbol Versions",
    "reasoning_code_links": "Reason-Code Links",
    "kuzu_write": "Session Graph Write",
    "central_version_merge": "Central Version Merge",
    "retrieval_docs": "Retrieval Docs",
    "embeddings": "Embeddings",
    "faiss": "FAISS",
    "quality_eval": "Quality Eval",
}

RESET_MARKER_KEY = "production_marker"
