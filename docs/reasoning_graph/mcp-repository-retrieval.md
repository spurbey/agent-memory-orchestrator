# MCP Repository Retrieval

AMO exposes one agent-facing repository memory search path through MCP:

```text
amo_graph_search(query, repo_id="", limit=8, use_vector=true, require_vector=false)
```

The tool is intentionally the only MCP retrieval surface for coding agents. Older
local-memory/context-pack, work-history, decision-history, and raw-evidence tools
are not registered on the MCP server because they let agents choose inconsistent
retrieval paths. Repository memory should come from the active production projection.

## Retrieval Flow

```text
agent query
-> amo_graph_search
-> repo alias resolution
-> daemon /graph/retrieve
-> active repo-scoped retrieval projection
-> BM25 + FAISS/vector candidates
-> deterministic + bi-encoder + lexical reranking
-> public MCP hits for synthesis
```

`amo_graph_search` returns retrieval context, not final prose. The consuming
agent should synthesize the answer from the returned `hits` and
`version_history`.

## Repo Resolution

Agents do not need to know canonical repo ids.

Accepted inputs:

```text
repo_id=""                         -> latest active repo projection
repo_id="agent-memory-orchestrator" -> active projection whose repo_path basename matches
repo_id="repo:remote:..."           -> exact canonical repo id
```

The returned payload always includes the resolved canonical repo id:

```json
{
  "repo": {
    "name": "agent-memory-orchestrator",
    "id": "repo:remote:311ebb9cda1fb40f"
  },
  "retrieval_status": {
    "source": "v2_active_projection",
    "repo_id_inferred": true
  }
}
```

## Public Output Shape

The MCP result is designed for other LLM agents. It avoids internal graph ids
and exposes stable, useful context:

```json
{
  "ok": true,
  "query": "why was demo_greet.py created",
  "retrieval_mode": "v2_active_repository_memory",
  "context_for_synthesis": "Use these retrieved memory hits to answer the user. Do not treat this as final prose.",
  "hits": [
    {
      "rank": 1,
      "kind": "file_impact",
      "title": "Impact summary for src/agent_memory_orchestrator/demo_greet.py",
      "summary": "User goal: ... Rationale: ... Validation: ...",
      "why_it_matched": "Matched curated code/file impact support for ...",
      "status": "file_impact_summary",
      "commit": {"sha": "a2a4803", "message": "Add AMO demo greeting"},
      "files": ["src/agent_memory_orchestrator/demo_greet.py"],
      "evidence": [
        {"role": "user_goal", "summary": "..."},
        {"role": "rationale", "summary": "..."},
        {"role": "validation", "summary": "..."}
      ]
    }
  ],
  "version_history": []
}
```

Internal ids such as `WP0001`, `kver:...`, graph node ids, graph commit ids, and
raw evidence ids are not part of the public MCP hit contract. Commits and file
paths are retained because they are stable and useful to agents.

## Evidence Enrichment

The top retrieval hit is often a support object such as `FileImpactSummary`,
`CodeImpactSummary`, `FileRef`, `SymbolRef`, or `CodeRegionRef`. Those support
objects may not directly carry the original user goal or validation output.

To avoid returning only commit/file summaries, MCP enriches support hits from
packet/reasoning documents in the active retrieval DB. The lookup is scoped by
repo and commit, not by packet id alone, because packet ids such as `WP0001`
repeat across jobs.

Priority for public evidence:

```text
problem_refs     -> role=user_goal
rationale_refs   -> role=rationale
validation_refs  -> role=validation
```

For support hits, the public summary prefers this evidence:

```text
User goal: ... Rationale: ... Validation: ...
```

This lets the agent answer why code changed, not just which files changed.

## Version History Rendering

Dashboard/API retrieval also builds a deterministic version timeline when a
query asks about history/evolution or targets a code locator with multiple
matching commits.

The timeline prefers curated `FileImpactSummary` and `CodeImpactSummary` rows
because central file/commit `KnowledgeVersion` rows are identity facts. They say
that a file or commit exists in active memory; they do not explain why the code
evolved. File/code impact rows carry commit messages, reasons, files, packets,
and support labels.

## Exposed MCP Tools

Repository retrieval should use only:

```text
amo_graph_search
```

Operational/debug tools may remain for lifecycle and peer-room inspection, but
agents should not use them as alternate repository search paths.

## Failure Modes

`active_repo_projection_missing` means no active production retrieval projection exists
for the resolved repo. Build/apply the curated production retrieval projection first.

`daemon_unavailable` means the MCP process could not reach the AMO daemon. Start
or restart the daemon/MCP process.

If an existing Codex session still shows old MCP tools, it is using a stale MCP
server process. Restart the MCP server or the Codex session so the current tool
registration is loaded.
