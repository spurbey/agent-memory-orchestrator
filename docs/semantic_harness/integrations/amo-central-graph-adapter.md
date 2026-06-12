# AMO Central Graph Adapter

## Purpose

Translate existing AMO central GraphView and curated session graph into harness-owned graph identities during migration.

## Identity Mapping

Harness IDs are deterministic and content-derived. AMO IDs are preserved through `ExternalAmoRef` and `IMPORTED_FROM_AMO`.

Mapping examples:

```text
AMO KnowledgeAtom atom_kind=file -> harness File
AMO KnowledgeVersion atom_kind=file -> harness FileVersion
AMO KnowledgeAtom atom_kind=symbol -> harness Symbol when canonical key is complete
AMO ReasoningNode -> harness ReasoningFrame or ExternalAmoRef, depending on review state
AMO GraphView -> import source metadata, not primary active-view identity
```

## Conflict Handling

- Complete deterministic match: import and link provenance.
- Missing file/symbol details: create ExternalAmoRef only.
- Conflicting canonical key: review-only mapping.
- Stale AMO result without applied merge result: diagnostic only.

## Output

The adapter emits graph update deltas and import mapping reports. It must not destructively rewrite AMO stores.
