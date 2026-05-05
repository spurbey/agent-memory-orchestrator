from __future__ import annotations

from agent_memory_orchestrator.chunker import chunk_text, classify_content_type
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
