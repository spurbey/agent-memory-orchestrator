# Example: Real AMO Partial-History Flow

## Fixture

This fixture is grounded in a smaller real completed AMO production job:

```text
session_dir = 019e76e1-98dd-75d1-8e8f-046679339444-3c901ff2
job_id = v2job:3c901ff20e08a147109af56a301c9207
repo_id = repo:remote:311ebb9cda1fb40f
artifact_dir = <amo_home>/.state/production-jobs/production-2026-05/019e76e1-98dd-75d1-8e8f-046679339444-3c901ff2/3c901ff20e08a147109af56a301c9207
```

Artifact evidence:

```text
curated_graph_manifest.json: 26 nodes, 32 edges
packets: 2
commits: 2
accepted reasoning nodes: 1
central merge: applied
retrieval_source: curated_graph_manifest
active_projection_id: rproj:fb0be7e05ded6c4920d972002e92030e
retrieval docs at stage: 6521
central active docs at stage: 1220
FAISS: completed, 6521 items, hash-fallback dims=16
```

This is a partial-style fixture because it is structurally useful but semantically narrow: it has only one accepted decision, limited packets, and code support that should guide inspection without pretending the whole retrieval system history is explained.

## Real Work Slice

The query slice comes from packet `WP0001`:

```text
commit = 30ba86e
commit_message = fix(retrieval): enforce strict code locator ranking
changed_files = src/agent_memory_orchestrator/application/services/retrieval/query.py; src/agent_memory_orchestrator/domain/retrieval/ranking.py; tests/application/retrieval/test_query_service.py
accepted_reasoning = The first staged commit includes only the ranking and filtering part of the retrieval system.
```

The accepted reasoning is useful but narrow. It does not explain every retrieval behavior or full answer-trace pipeline.

## Harness Query

```json
{
  "intent": "file_context",
  "user_goal": "understand strict code locator ranking retrieval behavior before editing",
  "anchors": {
    "files": ["src/agent_memory_orchestrator/domain/retrieval/ranking.py"],
    "symbols": ["_strict_code_locator_match"],
    "commits": ["30ba86e"],
    "errors": [],
    "recent_tool_result": {}
  },
  "budget": {"max_cards": 3, "max_tokens": 650, "detail": "strict"},
  "session_state": {"already_seen_node_ids": [], "already_seen_relation_ids": [], "already_seen_card_ids": []}
}
```

Expected status:

```text
partial_historical
```

Why `partial_historical`: the fixture has exact file/symbol/code-region support and one accepted decision, but only a narrow semantic frame. The harness can guide inspection of ranking functions, but it should not claim complete retrieval-system causality from this fixture alone.

## Current Retrieval Evidence

A repo-scoped retrieval smoke for `strict code locator ranking retrieval` returned these top product docs:

```text
1. FileImpactSummary: src/agent_memory_orchestrator/domain/retrieval/ranking.py
   packet_id = WP0001
   commit_sha = 30ba86e
   reason = first staged commit includes only ranking and filtering

2. FileImpactSummary: src/agent_memory_orchestrator/application/services/retrieval/query.py
   packet_id = WP0001
   commit_sha = 30ba86e

3. SymbolRef: src/agent_memory_orchestrator/domain/retrieval/ranking.py::_strict_code_locator_match
   impact_role = primary_implementation

4. SymbolRef: src/agent_memory_orchestrator/domain/retrieval/ranking.py::_strict_code_locator_terms
   impact_role = primary_implementation

5. CodeRegionRef: src/agent_memory_orchestrator/domain/retrieval/ranking.py::_strict_code_locator_match
   impact_role = primary_implementation
```

## Expected Harness Cards

```text
1. Inspect retrieval/ranking.py strict locator helpers.
   Why: curated symbol and code-region refs identify _strict_code_locator_match and _strict_code_locator_terms as primary implementation support for commit 30ba86e.
   Evidence: SymbolRef 3c901ff20e08:symref:723e2ff16d9c80681d8c; SymbolRef 3c901ff20e08:symref:adfbb9c250e8c8a425c2; CodeRegionRef 3c901ff20e08:coderef:008907a9af9579be5095.
   Confidence: 0.78
   Next action: inspect ranking.py helpers before editing retrieval query flow.

2. Treat the semantic reason as narrow.
   Why: accepted reasoning says this staged commit covers ranking and filtering only, not the whole retrieval pipeline.
   Evidence: ReasoningNode reason:WP0001:30ba86e:00:5682450f1417; commit 30ba86e.
   Confidence: 0.71
   Next action: do not infer answer-trace or vector behavior from this fixture without another query.

3. Check query.py only as the connected caller/support path.
   Why: FileImpactSummary shows query.py changed in the same commit, but ranking.py carries the strict locator helpers.
   Evidence: FileImpactSummary 3c901ff20e08:fileimpact:b832e33757a1c74dab93; FileImpactSummary 3c901ff20e08:fileimpact:326ae68681687ba26372.
   Confidence: 0.68
   Next action: inspect query.py after ranking.py if behavior is not contained in strict locator ranking.
```

## Expected Agent Behavior

The agent should use the structural and narrow historical guidance, but avoid claiming full retrieval architecture history from this session. This fixture demonstrates the `partial_historical` status: there is useful accepted reasoning, but it is intentionally scoped to one staged ranking/filtering commit.
