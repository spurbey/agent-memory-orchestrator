# Example: Real AMO Rich-History Flow

## Fixture

This fixture is grounded in a real completed AMO production job:

```text
session_dir = 019e5093-2b25-7d21-8238-2c94614a19da-b387ce0f
job_id = v2job:b387ce0faad2faf4885bd1267106071b
repo_id = repo:remote:311ebb9cda1fb40f
artifact_dir = <amo_home>/.state/production-jobs/production-2026-05/019e5093-2b25-7d21-8238-2c94614a19da-b387ce0f/b387ce0faad2faf4885bd1267106071b
```

Artifact evidence:

```text
curated_graph_manifest.json: 230 nodes, 292 edges
packets: 20
commits: 20
accepted reasoning nodes: 16
central merge: applied
retrieval_source: curated_graph_manifest
active_projection_id: rproj:4bd1501186f83d89d8ada22ed7e4bfc0
retrieval docs at stage: 6980
central active docs at stage: 1343
FAISS: completed, 6980 items, hash-fallback dims=16
```

This is a rich-history fixture because it has multi-commit work, accepted decisions, curated code/file support, central merge output, active retrieval projection, and complete vector cache for that projection.

## Real Work Slice

The query slice comes from packet `WP0003`:

```text
commit = 4f480a4
commit_message = fix(peer-agent): summarize current context shape deterministically
primary_file = src/agent_memory_orchestrator/peer/agent/service_utils.py
accepted_reasoning = Fix peer-answer prompt and responder LLM draft timeout
```

Accepted reasoning statement:

```text
The peer-answer prompt is being fixed to compact the prompt and cap the responder LLM draft timeout to prevent excessive request sizes and timeouts.
```

## Harness Query

```json
{
  "intent": "edit_plan",
  "user_goal": "fix peer answer prompt deterministic context shape",
  "anchors": {
    "files": ["src/agent_memory_orchestrator/peer/agent/service_utils.py"],
    "symbols": [],
    "commits": ["4f480a4"],
    "errors": [],
    "recent_tool_result": {}
  },
  "budget": {"max_cards": 3, "max_tokens": 700, "detail": "strict"},
  "session_state": {"already_seen_node_ids": [], "already_seen_relation_ids": [], "already_seen_card_ids": []}
}
```

Expected status:

```text
ready
```

Why `ready`: the fixture has exact commit/file anchors, accepted reasoning, curated FileImpactSummary, CodeImpactSummary, Packet support, and answer-grade ReasoningNode support.

## Current Retrieval Evidence

A repo-scoped retrieval smoke for `peer answer prompt deterministic context shape` returned these top product docs:

```text
1. FileImpactSummary: src/agent_memory_orchestrator/peer/agent/service_utils.py
   packet_id = WP0003
   commit_sha = 4f480a4
   reason = compact peer-answer prompt and cap responder LLM draft timeout

2. CodeImpactSummary: WP0003
   selected_files = src/agent_memory_orchestrator/peer/agent/service_utils.py
   impact_role = primary_implementation

3. Packet: WP0003 fix(peer-agent): summarize current context shape deterministically

4. ReasoningNode: Decision: Fix peer-answer prompt and responder LLM draft timeout
   evidence_refs = E00018, E00060
   promotion_grade = answer_grade
```

## Expected Harness Cards

```text
1. Inspect service_utils.py peer-answer context shaping.
   Why: accepted reasoning and curated impact link this file to compacting the peer-answer prompt and capping responder draft timeout.
   Evidence: FileImpactSummary b387ce0faad2:fileimpact:aaa1a98d0687ea8da47b; CodeImpactSummary b387ce0faad2:impact:WP0003:4f480a4; commit 4f480a4.
   Confidence: 0.86
   Next action: open src/agent_memory_orchestrator/peer/agent/service_utils.py before changing peer answer prompting.

2. Preserve the deterministic context-shape boundary.
   Why: the accepted Decision node says the bug was excessive prompt size and timeout behavior, not generic peer-agent failure.
   Evidence: ReasoningNode b387ce0faad2:reason:WP0003:4f480a4:00:12300aeb348b; evidence E00018 and E00060.
   Confidence: 0.84
   Next action: keep edits focused on compact context shaping and timeout capping.

3. Check adjacent prompt compaction work before broad edits.
   Why: related packet WP0005 also touches peer prompt compaction and may affect the same user-visible behavior.
   Evidence: Packet b387ce0faad2:WP0005; commit ea24695.
   Confidence: 0.73
   Next action: inspect peer prompt code only if service_utils.py alone does not explain the behavior.
```

## Expected Agent Behavior

The agent should inspect `service_utils.py` first, preserve the narrow bug boundary, and avoid starting with unrelated peer room architecture. This fixture demonstrates the harness returning compact high-confidence cards from current AMO memory instead of a broad retrieval dump.
