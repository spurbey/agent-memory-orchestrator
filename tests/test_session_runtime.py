from __future__ import annotations

from agent_memory_orchestrator.reasoning_graph.session_runtime import _model_embedding_dimension


def test_model_embedding_dimension_prefers_current_sentence_transformers_api() -> None:
    class Model:
        def get_embedding_dimension(self) -> int:
            return 1024

        def get_sentence_embedding_dimension(self) -> int:  # pragma: no cover - should not be called
            raise AssertionError("legacy API should not be called when current API exists")

    assert _model_embedding_dimension(Model()) == 1024


def test_model_embedding_dimension_falls_back_to_legacy_api() -> None:
    class Model:
        def get_sentence_embedding_dimension(self) -> int:
            return 768

    assert _model_embedding_dimension(Model()) == 768
