from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness.query_modes import RankToolHitsResult
from agent_memory_orchestrator.domain.semantic_harness.query_modes import RankedToolHit
from agent_memory_orchestrator.domain.semantic_harness.query_modes import RankedToolLine
from agent_memory_orchestrator.runtime.codex_proxy import CapturedProxyToolOutput
from agent_memory_orchestrator.runtime.codex_proxy import mutate_ranked_tool_outputs


def test_mutates_wrapped_responses_tool_output_after_raw_store() -> None:
    stored: dict[str, str] = {}
    payload = {
        "type": "response.create",
        "response": {
            "model": "gpt-test",
            "input": [
                {
                    "type": "local_shell_call_output",
                    "call_id": "call_1",
                    "output": "src/a.py:10:def target():\nsrc/b.py:20:def other():",
                }
            ],
        },
    }

    result = mutate_ranked_tool_outputs(
        payload,
        raw_store=lambda raw_ref, text: _store(stored, raw_ref, text),
        ranker=_ranker,
    )

    assert result.modified is True
    assert result.raw_refs == tuple(stored)
    output = result.payload["response"]["input"][0]["output"]
    assert output.startswith("AMO_RANKED_TOOL_HITS")
    assert "1. src/a.py score=0.9100 matches=1" in output
    assert f"RAW_OUTPUT_REF {result.raw_refs[0]}" in output
    assert "RAW_OUTPUT_EXCERPT" in output
    assert payload["response"]["input"][0]["output"].startswith("src/a.py")


def test_fails_open_when_raw_store_missing() -> None:
    payload = {"input": [{"type": "local_shell_call_output", "call_id": "call_1", "output": "src/a.py:1:x"}]}

    result = mutate_ranked_tool_outputs(payload, raw_store=None, ranker=_ranker)

    assert result.modified is False
    assert result.payload is payload
    assert result.warnings == ("raw_store_missing",)


def test_fails_open_when_raw_store_fails() -> None:
    payload = {"input": [{"type": "local_shell_call_output", "call_id": "call_1", "output": "src/a.py:1:x"}]}

    result = mutate_ranked_tool_outputs(payload, raw_store=lambda _ref, _text: False, ranker=_ranker)

    assert result.modified is False
    assert result.payload is payload
    assert result.warnings == ("raw_store_failed",)


def test_ignores_non_search_tool_output() -> None:
    payload = {"input": [{"type": "local_shell_call_output", "call_id": "call_1", "output": "plain output"}]}

    result = mutate_ranked_tool_outputs(payload, raw_store=lambda _ref, _text: True, ranker=_ranker)

    assert result.modified is False
    assert result.payload is payload
    assert result.raw_refs == ()
    assert result.warnings == ()


def test_ranker_exception_does_not_mutate_payload() -> None:
    payload = {"input": [{"type": "function_call_output", "call_id": "call_1", "output": "src/a.py:1:x"}]}

    def broken_ranker(_captured: CapturedProxyToolOutput) -> RankToolHitsResult:
        raise RuntimeError("boom")

    result = mutate_ranked_tool_outputs(payload, raw_store=lambda _ref, _text: True, ranker=broken_ranker)

    assert result.modified is False
    assert result.payload is payload
    assert result.warnings == ("ranker_failed",)


def _store(storage: dict[str, str], raw_ref: str, text: str) -> bool:
    storage[raw_ref] = text
    return True


def _ranker(captured: CapturedProxyToolOutput) -> RankToolHitsResult:
    return RankToolHitsResult(
        status="ready",
        ranked_hits=(
            RankedToolHit(
                path="src/a.py",
                file_node_id="file:repo:src/a.py",
                score=0.91,
                match_count=1,
                line_refs=(RankedToolLine(file_path="src/a.py", line=10, text="def target():"),),
                symbol_node_ids=("symbol:repo:src/a.py:target:function",),
                semantic_similarity=0.8,
                semantic_doc_ids=("doc:a",),
                reason_codes=("rg_match_strength:0.70", "candidate_local_semantic_similarity:0.80"),
            ),
        ),
        query_text="target",
        raw_ref=captured.raw_ref,
        embedding_backend="hash_token_char_cosine_v1",
        warnings=(),
    )
