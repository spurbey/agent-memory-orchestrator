from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True, frozen=True)
class ModelPreset:
    name: str
    embedding_model: str
    reranker_model: str
    qwen_model: str
    vector_backend: str
    recommended_for: str
    notes: str


MODEL_PRESETS: dict[str, ModelPreset] = {
    "cpu-light": ModelPreset(
        name="cpu-light",
        embedding_model="BAAI/bge-small-en-v1.5",
        reranker_model="cross-encoder/ms-marco-MiniLM-L-6-v2",
        qwen_model="qwen3:1.7b",
        vector_backend="faiss",
        recommended_for="CPU-only laptops or low-memory machines.",
        notes="Fast local setup; use --qwen-model qwen3:0.6b only when 1.7b cannot load.",
    ),
    "cpu-balanced": ModelPreset(
        name="cpu-balanced",
        embedding_model="BAAI/bge-m3",
        reranker_model="BAAI/bge-reranker-base",
        qwen_model="qwen3:4b",
        vector_backend="faiss",
        recommended_for="Modern CPU machines with roughly 8-16 GB RAM available.",
        notes="Recommended default for local production quality.",
    ),
    "gpu-quality": ModelPreset(
        name="gpu-quality",
        embedding_model="BAAI/bge-m3",
        reranker_model="BAAI/bge-reranker-large",
        qwen_model="qwen3:8b",
        vector_backend="faiss",
        recommended_for="GPU or high-RAM workstation where reranking quality matters more than latency.",
        notes="Higher-quality reranking, heavier model load and slower CPU fallback.",
    ),
}


def list_model_presets() -> list[dict[str, Any]]:
    return [_preset_payload(preset) for preset in MODEL_PRESETS.values()]


def resolve_models(
    *,
    preset: str | None = None,
    embedding_model: str | None = None,
    reranker_model: str | None = None,
    qwen_model: str | None = None,
) -> dict[str, str]:
    selected = MODEL_PRESETS.get((preset or "cpu-balanced").strip())
    if selected is None:
        raise ValueError(f"unknown model preset: {preset}")
    return {
        "preset": selected.name,
        "embedding_model": (embedding_model or selected.embedding_model).strip(),
        "reranker_model": (reranker_model or selected.reranker_model).strip(),
        "qwen_model": (qwen_model or selected.qwen_model).strip(),
        "vector_backend": selected.vector_backend,
    }


def model_status(
    *,
    preset: str | None = None,
    embedding_model: str | None = None,
    reranker_model: str | None = None,
    qwen_model: str | None = None,
    load_check: bool = False,
) -> dict[str, Any]:
    resolved = resolve_models(
        preset=preset,
        embedding_model=embedding_model,
        reranker_model=reranker_model,
        qwen_model=qwen_model,
    )
    embedding = _single_model_status(resolved["embedding_model"], "embedding", load_check=load_check)
    reranker = _single_model_status(resolved["reranker_model"], "reranker", load_check=load_check)
    return {
        "preset": resolved["preset"],
        "qwen_model": resolved["qwen_model"],
        "models": {
            "embedding": embedding,
            "reranker": reranker,
        },
        "ok": bool(embedding["available"] and reranker["available"]),
        "env": _env_for(resolved),
    }


def download_models(
    *,
    preset: str | None = None,
    embedding_model: str | None = None,
    reranker_model: str | None = None,
    qwen_model: str | None = None,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    resolved = resolve_models(
        preset=preset,
        embedding_model=embedding_model,
        reranker_model=reranker_model,
        qwen_model=qwen_model,
    )
    results = {
        "embedding": _download_model(resolved["embedding_model"], "embedding", cache_dir),
        "reranker": _download_model(resolved["reranker_model"], "reranker", cache_dir),
    }
    return {
        "preset": resolved["preset"],
        "models": results,
        "ok": all(item["ok"] for item in results.values()),
        "env": _env_for(resolved),
    }


def preflight_models(
    *,
    preset: str | None = None,
    embedding_model: str | None = None,
    reranker_model: str | None = None,
    qwen_model: str | None = None,
) -> dict[str, Any]:
    return model_status(
        preset=preset,
        embedding_model=embedding_model,
        reranker_model=reranker_model,
        qwen_model=qwen_model,
        load_check=True,
    )


def _preset_payload(preset: ModelPreset) -> dict[str, str]:
    return {
        "name": preset.name,
        "embedding_model": preset.embedding_model,
        "reranker_model": preset.reranker_model,
        "qwen_model": preset.qwen_model,
        "vector_backend": preset.vector_backend,
        "recommended_for": preset.recommended_for,
        "notes": preset.notes,
    }


def _single_model_status(model_name: str, role: str, *, load_check: bool) -> dict[str, Any]:
    cache = _hf_cache_status(model_name)
    result: dict[str, Any] = {
        "role": role,
        "model": model_name,
        "available": bool(cache["cached"]),
        "cache": cache,
        "load_checked": False,
        "load_error": "",
    }
    if load_check:
        loaded, error = _load_model_local(model_name, role)
        result["load_checked"] = True
        result["available"] = loaded
        result["load_error"] = error
    return result


def _hf_cache_status(model_name: str) -> dict[str, Any]:
    try:
        from huggingface_hub import try_to_load_from_cache  # type: ignore
    except Exception as exc:
        return {"cached": False, "path": "", "reason": f"huggingface_hub_unavailable:{exc}"}
    try:
        cached = try_to_load_from_cache(model_name, "config.json")
    except Exception as exc:
        return {"cached": False, "path": "", "reason": str(exc)}
    if isinstance(cached, str):
        return {"cached": True, "path": cached, "reason": ""}
    return {"cached": False, "path": "", "reason": "config_not_found_in_cache"}


def _load_model_local(model_name: str, role: str) -> tuple[bool, str]:
    try:
        if role == "embedding":
            from sentence_transformers import SentenceTransformer  # type: ignore

            SentenceTransformer(model_name, local_files_only=True)
        else:
            from sentence_transformers import CrossEncoder  # type: ignore

            CrossEncoder(model_name, local_files_only=True)
    except TypeError:
        return False, "installed sentence-transformers version does not support local_files_only"
    except Exception as exc:
        return False, str(exc)
    return True, ""


def _download_model(model_name: str, role: str, cache_dir: Path | None) -> dict[str, Any]:
    try:
        if role == "embedding":
            from sentence_transformers import SentenceTransformer  # type: ignore

            SentenceTransformer(model_name, cache_folder=str(cache_dir) if cache_dir else None)
        else:
            from sentence_transformers import CrossEncoder  # type: ignore

            CrossEncoder(model_name, cache_dir=str(cache_dir) if cache_dir else None)
    except Exception as exc:
        return {"role": role, "model": model_name, "ok": False, "error": str(exc)}
    return {"role": role, "model": model_name, "ok": True, "error": ""}


def _env_for(resolved: dict[str, str]) -> dict[str, str]:
    return {
        "AMO_EMBEDDING_MODEL": resolved["embedding_model"],
        "AMO_RERANKER_BACKEND": "cross-encoder",
        "AMO_RERANKER_MODEL": resolved["reranker_model"],
        "AMO_VECTOR_BACKEND": resolved["vector_backend"],
        "AMO_QWEN_MODEL": resolved["qwen_model"],
    }
