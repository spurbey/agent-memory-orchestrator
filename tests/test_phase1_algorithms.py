from __future__ import annotations

from agent_memory_orchestrator.chunker import chunk_text, classify_content_type
from agent_memory_orchestrator.cleaning import clean_event_text, should_promote_to_memory
from agent_memory_orchestrator.extraction import confidence_for_signal, extract_memory_candidates
from agent_memory_orchestrator.privacy import redact_secrets
from agent_memory_orchestrator.retrieval import reciprocal_rank_fusion, understand_query


def test_redaction_rules() -> None:
    redacted, changed = redact_secrets("token=abc123 password=hunter2 sk-1234567890abcdefghi")
    assert changed is True
    assert "abc123" not in redacted
    assert "hunter2" not in redacted
    assert "***REDACTED***" in redacted


def test_content_type_and_chunking_for_diff() -> None:
    diff = """diff --git a/retry.py b/retry.py
@@ -1,2 +1,2 @@
-delay = 1
+delay = backoff()
@@ -8,2 +8,2 @@
-jitter = 0
+jitter = random()
"""
    assert classify_content_type(diff) == "diff"
    chunks = chunk_text(diff)
    assert len(chunks) == 2
    assert chunks[0].metadata["path"] == "retry.py"


def test_rule_extraction_and_confidence_table() -> None:
    candidates = extract_memory_candidates(
        "Implemented scraper/retry.py exponential backoff with jitter and tests passed.",
        content_type="prose",
        event_type="response",
        agent="codex",
    )
    assert len(candidates) == 1
    assert candidates[0].memory_type == "fix"
    assert "scraper/retry.py" in candidates[0].entities
    assert confidence_for_signal("completed_fix") == 0.90
    assert confidence_for_signal("missing") == 0.40


def test_cleaning_strips_ide_context_and_marks_prompt_promotion() -> None:
    raw = """# Context from my IDE setup:

## Active file: Untitled9.md
## Open tabs:
- Untitled9.md: Untitled9.md

## My request for Codex:
final decision: use Codex hooks through UserPromptSubmit.
"""
    cleaned = clean_event_text(raw, event_type="prompt", agent="user")
    assert cleaned.text == "final decision: use Codex hooks through UserPromptSubmit."
    assert cleaned.metadata["amo_cleaning"]["removed_ide_context"] is True
    assert cleaned.metadata["amo_promote_memory"] is True
    assert "Open tabs" not in cleaned.text


def test_cleaning_suppresses_low_value_tool_output() -> None:
    text = 'Command completed: ["powershell.exe", "-Command", "rg \\"^# Cell\\" Untitled9.md"]\nOutput:\n# Cell 1'
    promote, reason = should_promote_to_memory(text, event_type="tool_result", agent="codex")
    assert promote is False
    assert reason == "low_value_tool_output"


def test_cleaning_suppresses_amo_search_diagnostic_output() -> None:
    text = (
        'Command completed: ["powershell.exe", "-Command", '
        '"python -m agent_memory_orchestrator.cli search --query hooks"]\n'
        'Output:\n{"ok": true, "results": [{"summary": "Updated the following files"}]}'
    )
    promote, reason = should_promote_to_memory(text, event_type="tool_result", agent="codex")
    assert promote is False
    assert reason == "diagnostic_tool_output"


def test_cleaning_suppresses_user_pasted_search_output() -> None:
    text = 'PS C:\\repo> python -m agent_memory_orchestrator.cli search --query hooks\n{"ok": true, "results": []}'
    promote, reason = should_promote_to_memory(text, event_type="prompt", agent="user")
    assert promote is False
    assert reason == "diagnostic_paste"


def test_extraction_does_not_turn_generic_should_explanation_into_decision() -> None:
    candidates = extract_memory_candidates(
        "That result means retrieval should now return the relevant memory, but this is only an explanation.",
        content_type="prose",
        event_type="response",
        agent="codex",
    )
    assert candidates[0].memory_type == "observation"


def test_query_understanding_and_rrf() -> None:
    exact = understand_query("what changed in scraper/retry.py", limit=5)
    assert exact.intent == "exact"
    assert exact.pools["bm25"] > exact.pools["vector"]

    causal = understand_query("why did retry jitter change", limit=5)
    assert causal.intent == "causal"
    assert causal.pools["kg"] > causal.pools["bm25"]

    fused = reciprocal_rank_fusion(
        {
            "bm25": [("a", 10.0), ("b", 9.0)],
            "vector": [("b", 0.8), ("c", 0.7)],
        }
    )
    assert fused["b"]["rrf_score"] > fused["a"]["rrf_score"]
