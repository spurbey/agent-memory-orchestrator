from __future__ import annotations

from pathlib import Path

import pytest

from agent_memory_orchestrator import model_manager
from agent_memory_orchestrator.cli import main


def test_model_presets_include_hardware_profiles() -> None:
    presets = {item["name"]: item for item in model_manager.list_model_presets()}
    assert "cpu-light" in presets
    assert "cpu-balanced" in presets
    assert "gpu-quality" in presets
    assert presets["cpu-balanced"]["embedding_model"] == "BAAI/bge-m3"
    assert presets["cpu-balanced"]["reranker_model"] == "BAAI/bge-reranker-base"
    assert presets["cpu-balanced"]["qwen_model"] == "qwen3:4b"


def test_resolve_models_allows_overrides() -> None:
    resolved = model_manager.resolve_models(
        preset="cpu-light",
        embedding_model="custom-embedding",
        reranker_model="custom-reranker",
        qwen_model="custom-qwen",
    )
    assert resolved["preset"] == "cpu-light"
    assert resolved["embedding_model"] == "custom-embedding"
    assert resolved["reranker_model"] == "custom-reranker"
    assert resolved["qwen_model"] == "custom-qwen"
    assert resolved["vector_backend"] == "faiss"


def test_model_status_uses_cache_and_optional_load_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        model_manager,
        "_hf_cache_status",
        lambda model_name: {"cached": model_name == "BAAI/bge-m3", "path": f"/cache/{model_name}", "reason": ""},
    )
    monkeypatch.setattr(model_manager, "_load_model_local", lambda model_name, role: (role == "embedding", "missing"))

    result = model_manager.model_status(preset="cpu-balanced", load_check=True)
    assert result["ok"] is False
    assert result["models"]["embedding"]["available"] is True
    assert result["models"]["embedding"]["load_checked"] is True
    assert result["models"]["reranker"]["available"] is False
    assert result["env"]["AMO_RERANKER_BACKEND"] == "cross-encoder"
    assert result["env"]["AMO_QWEN_MODEL"] == "qwen3:4b"


def test_download_models_calls_explicit_loaders(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, str, Path | None]] = []

    def fake_download(model_name: str, role: str, cache_dir: Path | None):
        calls.append((model_name, role, cache_dir))
        return {"role": role, "model": model_name, "ok": True, "error": ""}

    monkeypatch.setattr(model_manager, "_download_model", fake_download)
    result = model_manager.download_models(preset="cpu-light", cache_dir=tmp_path)
    assert result["ok"] is True
    assert calls == [
        ("BAAI/bge-small-en-v1.5", "embedding", tmp_path),
        ("cross-encoder/ms-marco-MiniLM-L-6-v2", "reranker", tmp_path),
    ]


def test_cli_models_list_outputs_presets(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["models", "list"])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert '"cpu-balanced"' in captured.out


def test_cli_models_preflight_fails_when_local_models_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        "agent_memory_orchestrator.cli.preflight_models",
        lambda **kwargs: {"ok": False, "models": {}, "env": {}, "preset": kwargs["preset"]},
    )
    exit_code = main(["models", "preflight", "--preset", "cpu-balanced"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert '"ok": false' in captured.out.lower()
