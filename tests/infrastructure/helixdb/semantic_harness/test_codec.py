from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import HarnessNode
from agent_memory_orchestrator.infrastructure.helixdb.semantic_harness.codec import node_from_properties
from agent_memory_orchestrator.infrastructure.helixdb.semantic_harness.codec import node_properties


def test_node_codec_preserves_semantic_metadata_and_anchor_fields() -> None:
    node = HarnessNode(
        id="symbol:repo:test:src/auth.py:login:function",
        kind="Symbol",
        label="login",
        repo_id="repo:test",
        metadata={
            "path": "src/auth.py",
            "qualified_name": "Auth.login",
            "line_start": 10,
            "line_end": 20,
            "semantic_facts": [{"fact_id": "fact:1", "text": "Keep fallback behavior."}],
        },
    )

    restored = node_from_properties(node_properties(node))

    assert restored == node
    assert restored.metadata["semantic_facts"][0]["fact_id"] == "fact:1"
