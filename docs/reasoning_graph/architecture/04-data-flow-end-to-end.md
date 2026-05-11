# End-To-End Data Flow

## Depends on
- 02-three-level-storage.md
- 03-runtime-ownership.md
- 05-failure-and-safety-model.md

## Used by
- ../implementation/01-phase-raw-timeline.md
- ../implementation/02-phase-session-graph.md
- ../implementation/05-phase-central-merge.md

## Related docs
- ../modules/session-timeline-builder.md
- ../modules/extraction-run-manager.md
- ../modules/central-graph-merge-engine.md

## Flow Summary

```text
Hook payloads + Codex transcript
  -> raw evidence ledger
  -> session timeline graph
  -> chunking and decision threads
  -> code diff/hunk/AST/code nodes
  -> decision extraction and relationship extraction
  -> versioned ExtractionRun session graph
  -> central graph merge on commit/finalize
  -> dependency propagation
  -> community detection
  -> graph inspection and future retrieval
```

## Step 1: Capture

Hooks append raw JSON evidence. `SessionStart` often includes `transcript_path`, which lets the daemon read full Codex rollout events, including visible assistant messages not captured directly by hooks.

## Step 2: Timeline Build

The daemon combines hook evidence and transcript events into ordered timeline nodes. It creates `FOLLOWED_BY` edges and records file/entity mentions.

## Step 3: Chunking

The daemon segments timeline events into chunks using file switches, explicit transition phrases, and semantic drift. Related chunks are merged into `DecisionThread` nodes when the agent revisits the same topic.

## Step 4: Code Analysis

For each write or finalized commit, the Git work ledger extracts changed files, diff hunks, patch id, and commit metadata. Hunks are expanded to AST-bounded `CodeNode`s where Tree-sitter can parse the language.

## Step 5: Decision Reasoning

Decision extraction runs on each decision thread. Rules handle clear patterns. Qwen handles only ambiguous extraction or relationship classification using strict schemas and confidence thresholds.

## Step 6: Session Graph Output

The graph builder writes a versioned `ExtractionRun`, session summary nodes, decisions, code nodes, tests, bugs, fixes, and typed edges. This graph is session-scoped and queryable before central merge.

## Step 7: Central Merge

Only selected/finalized extraction runs can merge into central graph. Entity resolution and decision dedupe classify whether new nodes duplicate, refine, supersede, contradict, or introduce new knowledge.

## Step 8: Propagation And Clustering

Superseded decisions trigger dependency propagation. Affected downstream nodes become `contested_pending_review`. Leiden clustering runs after central graph changes and stores support metadata.

## Step 9: Inspection

Graph inspection APIs expose why a file changed, caused-by chains, decision versions, active file decisions, contested nodes, extraction runs, and community membership.