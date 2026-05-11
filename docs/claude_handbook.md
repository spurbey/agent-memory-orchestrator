# Reasoning Graph System — Complete Handover Document
> Source handover note: this document preserves the original Claude handoff and
> discussion context. The normalized implementation authority is now
> `docs/reasoning_graph/`, which breaks this handoff into architecture, module,
> algorithm, graph-model, implementation, and example specs.

## What This System Is

Git tracks what changed in code. This system tracks **why** it changed — the full reasoning, decisions, and context that produced every code change, across every session, permanently.

This is a **persistent reasoning graph** that sits alongside Git. It is not a replacement for Git. It is a reasoning layer on top of it.

---

## The Three Levels of Storage

There are three distinct levels where knowledge lives. Each serves a different purpose.

```
Session Events (raw)
        ↓
Session Summary Graph (cleaned, per session)
        ↓
Central Graph (merged, all sessions, authoritative)
```

### Level 1 — Session Events (Raw)

Everything that happens during a session captured as-is. High noise, high fidelity. Temporary working material.

### Level 2 — Session Summary Graph

Processed version of the session events. Noise removed, decisions extracted, relationships typed, confidence scored. Scoped to one session. Persists permanently as historical record.

This is a **complete, standalone, queryable graph unit** — same structure as the central graph, just session-scoped. You can query it independently:
- "What decisions were made in session X?"
- "Why was NDK changed in this session?"
- "What files were touched and why?"

### Level 3 — Central Graph

The merged, reconciled, authoritative knowledge graph across all sessions. This is what future sessions query for context. Grows over time. Never shrinks — only appends.

---

## Level 1: What Gets Captured During a Session

### Agent Hooks

Four hooks fire during a session. Each writes to the `events` table.

**Hook: UserPromptSubmit**
```
event_type: user_message
content: exact text
timestamp
session_id
entities_mentioned: fast inline regex extraction
intent: classified by rule
```

**Hook: PostToolUse**
```
event_type: tool_use
tool_name: read_file | write_file | run_command | search | etc
tool_input: what the agent asked the tool to do
tool_output: what came back (file content, command output, error)
timestamp
files_affected: extracted from input and output
```

**Hook: Agent Message (each response)**
```
event_type: agent_message
content: exact text
reasoning_visible: chain-of-thought if exposed, captured separately
decisions_stated: phrases like "I'll pin NDK to 27 because..."
files_mentioned: extracted
entities_mentioned: extracted
```

**Hook: Stop (session end)**
```
event_type: session_end
final_state: last thing agent said
commits_made: list of commit SHAs produced this session
files_changed: aggregate list of all files touched
open_questions: anything agent flagged as unresolved
```

### Raw Session Graph Structure

The raw session graph is a directed timeline. Nodes are events. Edges are `FOLLOWED_BY` relationships preserving order.

```
user_message_1
    ──[FOLLOWED_BY]──► agent_message_1
    ──[FOLLOWED_BY]──► tool_use_1 (read_file: build.gradle.kts)
    ──[FOLLOWED_BY]──► tool_use_2 (run_command: flutter build)
    ──[FOLLOWED_BY]──► agent_message_2 ("I found the NDK mismatch...")
    ──[FOLLOWED_BY]──► tool_use_3 (write_file: build.gradle.kts)
    ──[FOLLOWED_BY]──► agent_message_3 ("Fixed by pinning to 27...")
```

All stored in the `events` table. No embeddings yet. No LLM yet. Pure capture.

---

## Level 2: Session Events → Session Summary Graph

This processing pipeline runs at the `Stop` hook. Five steps.

---

### Step 1: Chunking

A session is not one topic. It moves between files, concerns, problems. Chunking splits the session into coherent topic units before any extraction happens.

**Three signals determine chunk boundaries:**

**Signal 1 — File switch detection (pure rule, deterministic)**
When agent switches files, that is a natural boundary.
```
read(build.gradle.kts)     ← topic A
write(build.gradle.kts)
read(MainActivity.kt)      ← boundary, topic B starts
write(MainActivity.kt)
read(dashboard.html)       ← boundary, topic C starts
```

**Signal 2 — Explicit signal detection (rule-based pattern matching)**
Agent announces topic switches with phrases:
```
"Now let me look at..."
"Moving on to..."
"That's fixed, next issue is..."
"Actually let me check..."
```
Pattern match fires boundary immediately. No embeddings needed.

**Signal 3 — Semantic drift detection (embeddings)**
When no explicit signal exists, detect topic shift using a rolling window:
```
Take last 3 agent messages
Embed each one
cosine_similarity(window_N, window_N+1)

similarity < 0.65 → topic has shifted → chunk boundary
similarity ≥ 0.65 → same topic continues
```

This is the only place embeddings are used during chunking. Rules handle everything else.

**After boundaries are found — topic merging:**

When the agent revisits the same topic later in the session (e.g. goes back to NDK after fixing CSS):
```
chunk_1: NDK fix (10:04 - 10:11)
chunk_2: Sentry crash (10:11 - 10:24)
chunk_3: NDK again (10:31 - 10:38)
```

Detect revisit:
```
chunk_3 file matches chunk_1 file → candidate
embed chunk_3 topic vs chunk_1 topic → similarity 0.89
→ same topic → CONTINUES_TOPIC_OF edge created
→ merged into one decision thread at summary stage
```

Final output: not raw chunks, but **decision threads** — unified topic flows regardless of time gaps.

**Chunking summary:**
```
File switch detection    → pure rule
Explicit signal          → pure rule
Semantic drift           → embeddings (cosine on message windows)
Revisit detection        → embeddings (chunk topic similarity)
```

---

### Step 2: Code Diff → Hunks → AST → Code Nodes

For every file write captured in tool_use events, extract meaningful code units.

**Sub-step A: Git diff (Myers algorithm)**

Run diff between before and after the agent's write. Pure algorithmic diff. No LLM, no embeddings.

Example — agent reads then writes `build.gradle.kts`:

Before:
```kotlin
android {
    compileSdk = 33
    defaultConfig {
        minSdk = 21
        targetSdk = 33
    }
    ndk {
        version = "26.1.10909125"
        abiFilters += listOf("arm64-v8a", "x86_64")
    }
}
dependencies {
    implementation("com.mapbox:mapbox-sdk:10.1")
    implementation("com.google.firebase:firebase-core:21.0")
}
```

After:
```kotlin
android {
    compileSdk = 34
    defaultConfig {
        minSdk = 21
        targetSdk = 34
    }
    ndk {
        version = "27.0.12077973"
        abiFilters += listOf("arm64-v8a", "x86_64")
    }
}
dependencies {
    implementation("com.mapbox:mapbox-sdk:10.1")
    implementation("com.google.firebase:firebase-core:21.0")
    implementation("com.sentry:sentry-android:6.28.0")
}
```

Myers diff output — 4 hunks:
```
@@ -2,1 +2,1 @@       compileSdk 33 → 34
@@ -6,1 +6,1 @@       targetSdk 33 → 34
@@ -10,1 +10,1 @@     NDK version 26.1 → 27.0.12077973
@@ -16,1 +17,2 @@     Sentry dependency added
```

**Sub-step B: Tree-sitter AST parsing**

Parse the file into an AST. Map each hunk's line range to its AST parent node. Expand to the meaningful structural boundary.

```
Hunk 1: line 2
→ AST: compileSdk_assignment (standalone)
→ code_node_A: compileSdk_assignment, line 2

Hunk 2: line 6
→ AST: targetSdk_assignment inside defaultConfig_block (lines 4-7)
→ expand to parent block
→ code_node_B: defaultConfig_block, lines 4-7

Hunk 3: line 10
→ AST: version_assignment inside ndk_block (lines 9-12)
→ expand to parent block
→ code_node_C: ndk_block, lines 9-12

Hunk 4: line 19
→ AST: implementation_statement inside dependencies_block
→ single statement added, no need to expand
→ code_node_D: implementation_statement, line 19
```

**Sub-step C: Code node creation**

Each code node stores:
```
{
    file: "build.gradle.kts"
    type: "config_block" | "assignment" | "dependency_statement" | etc
    content: current snippet text
    prev_content: previous snippet text (null if new)
    line_range: [start, end]
    commit_sha: "61dae34"          ← pointer to Git, not full file
    session_id: current session
    embedding: [CodeBERT vector]   ← computed here
}
```

2000-3000 lines of changes → typically 40-80 meaningful code nodes. Not whole files. Not individual lines. AST-bounded meaningful units.

**The Git relationship:**

Git stores full file contents. The central graph stores only meaningful snippets with pointers back to commits. They are complementary:
```
Git answers:       "What does this file look like at commit X?"
Central graph answers: "Why does this block look the way it does?"
```

---

### Step 3: Decision Extraction

Scan every agent message in each decision thread for decision patterns.

**Rule-based extraction (handles ~70-80% of decisions):**
```
"I'll [action] because [reason]"
→ decision_type: planned_action
→ confidence: 0.60

"Fixed by [action]" + subsequent test pass
→ decision_type: completed_fix
→ confidence: 0.90

"The issue is [cause]"
→ decision_type: investigation_result
→ confidence: 0.80 if tool output confirms, 0.60 if agent-stated only

"Pinning [X] to [Y]"
→ decision_type: constraint
→ confidence: 0.75

"Reverting [X]"
→ decision_type: revert
→ confidence: 0.85
```

**LLM fallback (remaining ~20-30%):**
When no pattern matches but embedding similarity to known decision patterns is high, send to LLM:
```
input: agent message + nearby context
task: "Is there a decision being made here? Extract it."
output: structured decision or null
max_tokens: 200
```

Small, focused call. Not a full reasoning call.

**Confidence scoring rules:**
```
Agent stated plan, no evidence yet     → 0.60
Agent stated + tool output confirms    → 0.80
Fix applied + test passed              → 0.90
Human explicitly confirmed             → 1.00
```

---

### Step 4: Relationship Extraction Between Decisions

For each pair of decisions within a decision thread, determine typed relationship.

**CAUSED_BY — needs LLM (small focused call)**
```
agent_message: "pinning NDK to 27 because Sentry requires it"

Rule finds "because" pattern → extracts cause clause
LLM maps "Sentry requires it" to existing decision_node("Sentry SDK upgrade")
→ decision_NDK_pin ──[CAUSED_BY]──► decision_Sentry_upgrade
```

**SUPERSEDED_BY — rule-based**
```
Decision A: "NDK was at 26.1" (investigation, session start)
Decision B: "NDK pinned to 27.0.12077973" (fix, session end)
Same subject, different value, B comes after A temporally
→ A ──[SUPERSEDED_BY]──► B
```

**REVERTS — rule + pattern**
```
Agent message contains "reverting", "undoing", "rolling back"
+ same AST node changed as earlier decision
→ new_decision ──[REVERTS]──► old_decision
```

---

### Step 5: Decision-to-Code Linking

Link each decision to the code nodes it produced.

Detection method:
```
agent_message (decision) precedes tool_use write(file) in timeline
file write produces code_node_C
→ decision ──[PRODUCED_CHANGE_IN]──► code_node_C
code_node_C ──[LINKED_TO_COMMIT]──► commit_sha
```

If a single agent message preceded multiple file writes (agent made several changes for one reason), that decision links to multiple code nodes.

---

### Session Summary Graph Output

Written to these tables in KuzuDB:
```
decision_units          → decision nodes with type, content, confidence
decision_versions       → version history of decisions
decision_code_links     → decision → file + line_range + commit
kg_nodes                → all nodes for graph traversal
kg_edges                → all edges with types and weights
session_summaries       → human-readable session summary
```

This graph is permanent. It is the historical record of the session's reasoning. It is never modified after creation.

---

## Level 3: Session Summary Graph → Central Graph

This is the merge pipeline. Runs asynchronously after session ends. Four steps.

---

### Step 1: Entity Resolution

Before merging, determine whether entities in the session graph match existing central graph entities.

```python
def is_same_node(session_node, central_node):
    string_sim   = edit_distance(session_node.name, central_node.name)
    embed_sim    = cosine(session_node.embedding, central_node.embedding)
    struct_sim   = jaccard(neighbors(session_node), neighbors(central_node))

    score = 0.5 × string_sim + 0.3 × embed_sim + 0.2 × struct_sim

    if score > 0.85:  same entity → merge
    if score 0.65-0.85: probable → flag for review, tentatively merge
    if score < 0.65:  different entity → create new node
```

---

### Step 2: Decision Deduplication

For each decision in session graph, check if central graph already has it.

```
new_decision: "NDK pinned to 27.0.12077973 because Sentry requires it"

find candidates: decisions about NDK with PINNED_IN relationship

for each candidate:
    relatedness = 0.45×cosine + 0.25×lexical + 0.20×entity_jaccard + 0.10×same_topic

    relatedness > 0.85 AND same subject+predicate+object
    → exact duplicate → skip, add evidence link only

    relatedness > 0.65 AND same subject+predicate, different object
    → version conflict → classify relationship (next step)
```

---

### Step 3: Relationship Classification for Conflicts

When new session decision overlaps with central graph:

**SUPERSEDES:**
```
Central: "NDK pinned to 27.0.12077973" (active)
New:     "NDK upgraded to 28.2" + evidence that old constraint no longer applies
→ new ──[SUPERSEDES]──► old
→ old status: superseded
→ new status: active
```

**CONFLICTS_WITH:**
```
Central: "NDK pinned to 27.0.12077973" (active)
New:     "NDK should be 28.2" + no evidence old constraint resolved
→ new ──[CONFLICTS_WITH]──► old
→ both status: contested
→ flagged for human review
```

**REFINES:**
```
Central: "NDK should be pinned to 27 family"
New:     "NDK pinned to 27.0.12077973 specifically"
→ new ──[REFINES]──► old
→ old status: refined
→ new status: active
```

**Critical rule: Never delete. Only change status and add edges.** Full history always preserved. Query for active decisions only, or full history, or contested-only — all possible.

---

### Step 4: Dependency Propagation

When a decision is superseded, find everything that depended on it and flag it:

```
Decision D-047 (NDK 27 pin) → now superseded
Find all decisions with DEPENDS_ON edge to D-047
→ Decision D-052: "Mapbox works because NDK 27 ABI is available"
→ D-052 may now be invalid → flag as contested
```

This is graph traversal (BFS on KuzuDB). No LLM, no embeddings. Pure graph walk.

---

### Embedding Strategy Across Levels

Embeddings exist at two levels, serve different purposes, use different models.

**Session level (BGE-M3 for text, CodeBERT for code):**
- Used during extraction: finding which messages contain decisions
- Used during chunking: semantic drift detection
- Computed once, carried into central graph on the same nodes

**Central graph level:**
- Used for retrieval: future sessions finding relevant past decisions
- Used for entity resolution: matching new nodes to existing ones
- Used for deduplication: finding similar decisions

**Critical rule: Never update existing embeddings in central graph.** When a new session's decision supersedes an old one, the old node's embedding is untouched. A new node is added with its own embedding. Both remain queryable.

```
Central graph is append-only at the embedding level.
Edges change. Statuses change.
Embeddings, once written, never mutate.
```

---

### Community Detection with Leiden Algorithm

Runs periodically (not every session) to find clusters of related nodes.

Leiden finds communities by maximizing modularity — how densely connected nodes are within a group compared to random.

```python
import leidenalg, igraph as ig, networkx as nx

# Pull graph from KuzuDB
result = conn.execute("MATCH (a)-[r]->(b) RETURN a.id, b.id, r.weight")

G = nx.Graph()
for row in result:
    G.add_edge(row['a.id'], row['b.id'], weight=row['r.weight'])

ig_graph = ig.Graph.from_networkx(G)
partition = leidenalg.find_partition(
    ig_graph,
    leidenalg.ModularityVertexPartition,
    weights='weight'
)
# partition.membership → community_id per node
```

Edge weights used by Leiden:
```
CAUSED_BY edge         → 0.9  (strong causal)
SUPERSEDES edge        → 0.8
PRODUCED_BY edge       → 0.7
SAME_FILE edge         → 0.5
FOLLOWED_BY edge       → 0.2  (weak temporal)
```

Result — nodes grouped into communities:
```
community_0: NDK, Sentry, build.gradle.kts, compileSdk
→ label: "Android Build Configuration"

community_1: LoginScreen, CSS, dashboard.html
→ label: "Frontend Layout"

community_2: API endpoint, api_service.dart, auth
→ label: "Backend Integration"
```

Community IDs stored back on nodes in KuzuDB:
```cypher
MATCH (n:DecisionNode) WHERE n.id = $id
SET n.community_id = $community_id, n.community_label = $label
```

Why periodic not per-session: Leiden is expensive on large graphs. Communities are used for organization and querying, not real-time conflict detection. Runs every N sessions or when graph grows by X%.

---

### Same-File Chunk Resolution

When two chunks in the same session both change the same file, three possible situations:

**Situation 1 — Continuation (same topic, same AST nodes):**
```
chunk_1: "pinned NDK to 27, let me verify"
chunk_4: "build failed, adjusting to 27.0.12077973 specifically"

topic similarity = 0.88, same AST node changed
→ chunk_4 CONTINUES_TOPIC_OF chunk_1
→ merged into one decision thread
→ code_node_v1 ──[REFINED_BY]──► code_node_v2
```

**Situation 2 — Unrelated (different topic, same file):**
```
chunk_1: "fixing NDK version for Sentry"
chunk_4: "bumping compileSdk to 34 for new API"

topic similarity = 0.31, different AST nodes changed
→ separate decision threads
→ separate code nodes, separate decisions
→ no linking
```

**Situation 3 — Revert (same topic, same AST nodes, revert signal):**
```
chunk_1: "pinned NDK to 27.0.12077973"
chunk_4: "reverting NDK pin, breaks Mapbox"

topic similarity = 0.85, same AST node
agent message contains "reverting"
→ code_node_v1 ──[SUPERSEDED_BY]──► code_node_v2
→ decision_D047 ──[REVERTED_BY]──► decision_D051
Both preserved. History intact.
```

Resolution logic:
```
same file touched by earlier chunk?
    → check topic similarity

    similarity > 0.75 AND same AST nodes:
        revert signals present → REVERTS_DECISION
        no revert signals      → REFINES_DECISION

    similarity > 0.75 AND different AST nodes:
        → CONTINUES_TOPIC_OF, separate code nodes

    similarity < 0.75:
        → completely separate thread, no linking
```

---

## How Future Sessions Use the Central Graph

At session start:
```
SessionStart hook fires
→ load active decisions relevant to current workspace files
→ load contested decisions open for resolution
→ load recent session summaries
→ build startup briefing:

"Active constraints:
 - NDK: contested between pin-to-27 (D-047) and upgrade-to-28 (D-089)
 - Mapbox: requires NDK 27 ABI (D-052, depends on D-047)
 - Sentry: requires NDK 27 family (D-031, active)

 Open questions:
 - Can Mapbox work with NDK 28? (flagged session 019dacc7)
 - Is there a newer Sentry supporting NDK 28?

 Last session (2 days ago): Flutter upgrade blocked by NDK conflict"
```

New session starts knowing exactly where previous session left off. No re-investigation from scratch.

---

## Code Querying

When a code block is pasted into the system:

Two embedding spaces work together:
- **CodeBERT** — finds the location in the graph (code similarity)
- **BGE-M3** — explains the meaning at that location (decision similarity)

```
Pasted: ndk { version = "27.0.12077973" }

1. CodeBERT embeds the block
2. Find nearest code nodes in central graph
3. Traverse edges from matched node:
   → EXPLAINED_BY → "NDK pinned to 27 because Sentry requires NDK 27 family"
   → CHANGED_IN   → commit 61dae34, session 019dacc7
   → CAUSED_BY    → "Sentry SDK upgrade in session 004abc"
   → DEPENDED_ON_BY → "Mapbox assumes NDK 27 ABI is available"
```

For unseen code, semantic similarity finds related nodes and returns nearby reasoning context even without an exact match.

---

## What Each Technology Does

```
Myers diff algorithm     → hunk extraction from file writes (deterministic)
Tree-sitter              → AST parsing, expand hunks to structural boundaries
BGE-M3                   → text and decision embeddings
CodeBERT / UniXcoder     → code embeddings
Rule-based patterns      → decision extraction (70-80% of cases)
LLM (small focused)      → decision extraction fallback, CAUSED_BY edge detection
KuzuDB                   → graph storage, traversal, community_id storage
Leiden algorithm         → periodic community detection on central graph
Entity resolution        → scoring function (string + embed + structural)
Git                      → full file contents, blame, commit history
```

---

## What Git Does vs What This System Does

```
Git                              Central Graph
─────────────────────────────    ─────────────────────────────────
Stores full file snapshots       Stores meaningful code snippets only
Tracks what changed              Tracks why it changed
Diffs between commits            Decision chains across sessions
Blame by line                    Reasoning by decision
Linear history                   Graph with typed relationships
No conflict reasoning            Conflict detection and flagging
```

They work together. Central graph points to Git commits for full file contents. Git provides the ground truth of what the code is. Central graph provides the ground truth of why it is that way.

---

## The Full Pipeline at a Glance

```
DURING SESSION
─────────────────────────────────────────────────────────────────
UserPromptSubmit → capture user_message node
PostToolUse      → capture tool_use node + tag files affected
Agent response   → capture agent_message node + fast entity extract
Stop             → trigger processing pipeline

AT SESSION END
─────────────────────────────────────────────────────────────────
Chunking:
  file switch detection (rule)
  explicit signal detection (rule)
  semantic drift detection (embeddings)
  → topic chunks

Code extraction per chunk:
  git diff (Myers) → hunks
  tree-sitter → AST boundaries
  → code nodes with CodeBERT embeddings

Decision extraction per chunk:
  rule-based patterns (70-80%)
  LLM fallback (remainder)
  → decision nodes with confidence scores

Relationship extraction:
  FOLLOWED_BY (timestamp order)
  PRODUCED_BY (proximity + file match)
  CAUSED_BY (LLM, small focused)
  SUPERSEDED_BY (rule)
  REVERTS (rule + signal)

Session summary graph written to KuzuDB

MERGE (async after session)
─────────────────────────────────────────────────────────────────
Entity resolution → match or create nodes in central graph
Decision deduplication → find overlapping decisions
Relationship classification → supersedes / conflicts / refines
Dependency propagation → flag affected downstream decisions
Central graph updated (append-only on embeddings)

PERIODIC
─────────────────────────────────────────────────────────────────
Leiden runs → communities detected → community_id stored on nodes
Conflict report generated → contested decisions surfaced
Stale decision detection → no activity for N days
```