# Curated Session Graph and Central Merge Boundary

AMO keeps two graph products for a completed production job:

1. **Trace graph manifest**: exhaustive debug trace from packets, reasoning nodes, hunks, AST nodes, symbols, and code versions.
2. **Curated graph manifest**: answer-grade and support-grade memory used by central merge and retrieval.

The trace graph is not central memory. It exists so operators can audit how extraction behaved. The curated graph is the product-facing graph.

## Why This Exists

The production job `v2job:0b68249f48c244c68fb12977eb93d9ba` proved that a full trace graph can become too noisy:

- raw trace: `20086` nodes and `45506` edges
- curated graph after promotion policy: about `2400` nodes and `3100` edges

The large trace graph was not wrong as a debug artifact, but it was wrong as a retrieval and central-memory input. It linked decisions to many code fragments that were only trace evidence, such as import blocks, returns, assertions, and generated/support snippets.

## Stage Boundary

The production runner still builds the full compact graph manifest:

```text
kuzu_write/compact_graph_manifest.json
```

It also writes the curated graph:

```text
kuzu_write/curated_graph_manifest.json
kuzu_write/curation_audit.json
```

Central merge prefers the curated manifest:

```text
curated_graph_manifest.json -> central_version_merge
```

If no curated manifest exists, it falls back to the full compact manifest for legacy jobs.

## Promotion Policy

Code extraction is deterministic. Qwen creates packet reasoning before AST/code linking; it does not decide which AST nodes become memory.

Promotion grades:

```text
answer_grade  -> ReasoningNode decisions/problems/fixes
support_grade -> Commit, EvidenceRef, CodeImpactSummary, FileImpactSummary, FileRef, SymbolRef, CodeRegionRef
trace_only    -> imports, returns, assertions, unparsed hunks, docs/examples unless directly relevant
debug_only    -> other low-signal code fragments
```

Only answer-grade and support-grade nodes enter the curated graph. Full trace details remain in stage artifacts.

Code parsing is not Python-only. The default extraction path now has dependency-free structural parsers for:

```text
Python
JavaScript / TypeScript / JSX / TSX
CSS / SCSS / LESS
HTML / SVG / Vue / Svelte-style markup
JSON / YAML / TOML / env-like config
Markdown sections
common brace languages such as Dart, Java, Kotlin, Go, Rust, Swift, C/C++
```

The goal is not full compiler accuracy for every language. The goal is stable structural anchors for retrieval and central merge: functions/classes, style rules, markup elements, config keys, and documentation sections. If parsing is uncertain, the hunk remains trace-only.

## Curated Node Types

`CodeImpactSummary`

One node per work packet. It says which commit implemented the packet and which selected files/symbols/code regions matter.

It also carries semantic roles for the selected files:

```text
primary_implementation -> runtime code in Python/JS/TS/Dart/etc.
ui_style               -> CSS/SCSS/LESS style changes
ui_markup              -> HTML/SVG/Vue/Svelte-style markup changes
validation_test        -> tests and test fixtures
config                 -> config/env/package metadata
docs                   -> documentation sections
support                -> selected support files that do not fit the above
```

Roles are deterministic support metadata, not LLM judgments. They decide how retrieval should rank support docs and whether a symbol/code region can become an exact central atom.

`FileImpactSummary`

One node per selected file. It rolls up the packet-level code impacts for file-level questions such as:

```text
why did we change graph_service.py?
```

`FileRef`, `SymbolRef`, `CodeRegionRef`

Representative support nodes. They are not exhaustive AST trace. They point retrieval and central merge toward the meaningful code surface.

## Central Merge Contract

Central exact atom planning consumes curated nodes:

```text
Commit        -> commit atom
FileRef       -> file atom
SymbolRef     -> symbol atom only when marked primary_implementation
CodeRegionRef -> code_region atom only when marked primary_implementation
```

`CodeImpactSummary` and `FileImpactSummary` are support/provenance nodes. They are indexed for retrieval but are not exact canonical atoms.

This prevents central memory from becoming a second AST graph. Most symbols and
code regions stay as support refs attached to code impacts. Only high-signal,
non-test implementation targets become canonical atoms. UI style/markup, docs,
config, and validation tests remain searchable support unless later phases add a
reviewed reason to promote them.

Decision/problem evolution remains dry-run until the semantic matcher is proven safe.

Decision dry-run frames read curated context through:

```text
ReasoningNode -> CodeImpactSummary -> FileRef/SymbolRef/CodeRegionRef
```

The old direct `REASON_NODE_LINKED_TO_CODE_NODE` edge path is still supported for legacy manifests, but curated manifests should use the code-impact path.

## Reasoning Review Guard

Reasoning review has a semantic alignment signal before graph promotion.

If a Qwen node has no meaningful overlap with the commit message or changed
files, the node is marked `needs_review` instead of `accepted`. This is a
quarantine, not a hard reject. It catches cases where weak local models select a
valid-looking statement from noisy packet evidence but attach it to the wrong
commit.

The `WP0086` production probe is the reference case:

```text
commit: feat(graph-ui): add spatial graph controls
bad node: dashboard exclusively uses the production retrieval path
result: needs_review, not answer_grade
```

## Retrieval Contract

Retrieval should prefer:

1. active central memory if a repo-scoped `GraphView(main, active)` exists
2. curated session memory as support/fallback
3. trace-only artifacts only through explicit debug paths

For file-level questions, `FileImpactSummary` should rank above packet-level code impacts. Packet-level `CodeImpactSummary` then provides commit/packet trace.

## Production Probe

The current probe output is under:

```text
.tmp/curated-graph-eval/v2job-0b68249f/full_regen_v2_roles/
```

Observed semantic behavior:

- `WP0086` selects `amo.css`, `amo.js`, and `index.html` as code-impact files, but the bad Qwen reasoning text is quarantined.
- `why did we change graph_service.py?` ranks `FileImpactSummary` first.
- `what changed for spatial graph controls?` ranks the commit, packet-level `CodeImpactSummary`, and UI file impacts.
- `what is the current active graph service retrieval design?` no longer ranks low-level test symbol refs above packet/file summaries, but still needs central decision synthesis for a final answer.

This validates the graph boundary, not final answer synthesis. Answer synthesis still has to combine top retrieval hits into a concise explanation with packet, commit, evidence, and code trace.
