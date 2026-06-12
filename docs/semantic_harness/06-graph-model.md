# Graph Model

## Purpose

The graph model defines the harness-owned logical repo knowledge graph.

## Identity Rule

Harness IDs are deterministic and harness-owned. AMO IDs are provenance only.

## Node Families

### Repo

Repository identity and metadata.

### File

A normalized file path inside a repo.

### Symbol

A named language entity: function, class, method, variable, component, test, or exported config unit.

### CodeRegion

A bounded code area that may be smaller, broader, or less formal than a Symbol.

Examples:

- branch inside a large function
- React JSX block
- CSS selector group
- config stanza
- Markdown section

### Version Nodes

`FileVersion`, `SymbolVersion`, and `CodeRegionVersion` represent state at a commit or indexed snapshot.

### Work Nodes

`Commit`, `Hunk`, `WorkWindow`, `UserIntent`, `AgentAction`, `ToolObservation`, and `ReasoningFrame` connect work evidence to code changes.

### RelationOccurrence

One observed reason or event that contributed to a relation edge. Aggregate edge weight is stored separately from these occurrences.

### DocSection

A deterministic section extracted from repo documentation such as README and Markdown files. It stores heading, line range, and compact content excerpt. It is not produced by an LLM.

### DocString

A deterministic module, class, function, or method docstring extracted from source code. It documents the nearest file or symbol using parser-backed ownership.

### HarnessCard

A card returned to an agent.

Minimum fields:

```text
card_id
card_type
anchor_node_ids
support_node_ids
confidence_at_creation
session_id
intent_context
status: shown|suppressed|acted_on|ignored|invalidated
created_at
```

### ExternalAmoRef

A provenance node preserving AMO source IDs, graph IDs, evidence refs, job IDs, and session IDs.

## Edge Families

- `CONTAINS`: repo/file/symbol containment.
- `DEFINES`: file defines symbol.
- `IMPORTS`: file or symbol imports dependency.
- `CALLS`: symbol or code region calls another symbol.
- `CHANGED_IN`: file/symbol/region version changed in commit.
- `MAPS_TO_SYMBOL`: hunk maps to symbol.
- `MAPS_TO_CODE_REGION`: hunk maps to code region.
- `DERIVED_FROM_WORK_WINDOW`: derived node came from work window.
- `MOTIVATED_BY`: code change motivated by user intent or reasoning frame.
- `VALIDATED_BY`: work validated by test/build/check.
- `CO_CHANGED_WITH`: entities changed together across work.
- `RENAMED_TO`: entity renamed.
- `MOVED_TO`: entity moved file or scope.
- `SPLIT_INTO`: entity split into multiple entities.
- `MERGED_INTO`: entities merged.
- `DOCUMENTS_FILE`: docstring documents a file/module.
- `DOCUMENTS_SYMBOL`: docstring documents a symbol.
- `MENTIONS_FILE`: doc section or docstring explicitly mentions a repo file path.
- `MENTIONS_SYMBOL`: doc section or docstring explicitly mentions a known symbol label.
- `SUPPORTS_CARD`: node or edge supports a harness card.
- `IMPORTED_FROM_AMO`: harness node or edge links to AMO provenance.

## Promotion Rule

Raw AST nodes are not product memory. Only symbols, lazy code regions, versions, relation occurrences, and reviewed semantic frames become harness graph entities.
