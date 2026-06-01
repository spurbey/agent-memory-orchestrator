"""LLM and model infrastructure adapters."""

from __future__ import annotations

from .embeddings import cosine_similarity
from .embeddings import embed_text
from .embeddings import embed_text_with_model
from .models import download_models
from .models import list_model_presets
from .models import model_status
from .models import preflight_models
from .models import resolve_models
from .qwen import DeterministicPlanner
from .qwen import OllamaQwenClient
from .qwen import QueryPlan
from .qwen import QwenPlanner
from .qwen import QwenUnavailable
from .rerankers import RerankCandidate
from .rerankers import RerankResult
from .rerankers import rerank_candidates
from .text_embedder import StrictTextEmbedder
from .vector_cache import FaissBuildResult
from .vector_cache import FaissSearchResult
from .vector_cache import VectorRow
from .vector_cache import build_faiss_cache
from .vector_cache import search_faiss_cache

__all__ = [
    "DeterministicPlanner",
    "FaissBuildResult",
    "FaissSearchResult",
    "OllamaQwenClient",
    "QueryPlan",
    "QwenPlanner",
    "QwenUnavailable",
    "RerankCandidate",
    "RerankResult",
    "StrictTextEmbedder",
    "VectorRow",
    "build_faiss_cache",
    "cosine_similarity",
    "download_models",
    "embed_text",
    "embed_text_with_model",
    "list_model_presets",
    "model_status",
    "preflight_models",
    "rerank_candidates",
    "resolve_models",
    "search_faiss_cache",
]

