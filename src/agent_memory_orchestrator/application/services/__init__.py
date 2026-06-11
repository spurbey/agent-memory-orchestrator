"""Production application service boundaries."""

from __future__ import annotations

__all__ = [
    "CentralMergeRunResult",
    "CentralMergeService",
    "ConnectorRuntimeService",
    "EvidenceIngestService",
    "LocalAgentReviewService",
    "PeerAgentService",
    "ProductionPipelineService",
    "RETRIEVAL_EMBEDDING_KIND",
    "RetrievalQueryService",
    "SessionBoundaryService",
    "StructuralHarnessService",
    "build_session_detail_fallback",
    "embed_missing_retrieval_documents",
    "retrieve_session_graph",
    "vector_candidates",
]


def __getattr__(name: str):
    if name in {"CentralMergeRunResult", "CentralMergeService"}:
        from .central_merge import CentralMergeRunResult
        from .central_merge import CentralMergeService

        return {"CentralMergeRunResult": CentralMergeRunResult, "CentralMergeService": CentralMergeService}[name]
    if name == "ConnectorRuntimeService":
        from .connectors.runtime import ConnectorRuntimeService

        return ConnectorRuntimeService
    if name == "EvidenceIngestService":
        from .capture.evidence_ingest import EvidenceIngestService

        return EvidenceIngestService
    if name == "LocalAgentReviewService":
        from .review.local_agent import LocalAgentReviewService

        return LocalAgentReviewService
    if name == "ProductionPipelineService":
        from .pipeline.production import ProductionPipelineService

        return ProductionPipelineService
    if name == "PeerAgentService":
        from .peer.agent import PeerAgentService

        return PeerAgentService
    if name == "SessionBoundaryService":
        from .session.boundary import SessionBoundaryService

        return SessionBoundaryService
    if name == "StructuralHarnessService":
        from .semantic_harness import StructuralHarnessService

        return StructuralHarnessService
    if name == "build_session_detail_fallback":
        from .session.detail import build_session_detail_fallback

        return build_session_detail_fallback
    if name in {"RETRIEVAL_EMBEDDING_KIND", "embed_missing_retrieval_documents"}:
        from .retrieval.embedding import RETRIEVAL_EMBEDDING_KIND
        from .retrieval.embedding import embed_missing_retrieval_documents

        return {
            "RETRIEVAL_EMBEDDING_KIND": RETRIEVAL_EMBEDDING_KIND,
            "embed_missing_retrieval_documents": embed_missing_retrieval_documents,
        }[name]
    if name in {"RetrievalQueryService", "retrieve_session_graph"}:
        from .retrieval.query import RetrievalQueryService
        from .retrieval.query import retrieve_session_graph

        return {"RetrievalQueryService": RetrievalQueryService, "retrieve_session_graph": retrieve_session_graph}[name]
    if name == "vector_candidates":
        from .retrieval.vector import vector_candidates

        return vector_candidates
    raise AttributeError(name)
