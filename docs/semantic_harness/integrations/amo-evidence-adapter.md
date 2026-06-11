# AMO Evidence Adapter

## Purpose

Import AMO raw evidence and work-window evidence without making AMO IDs primary harness identity.

## Inputs

- raw evidence refs
- session IDs
- hook event windows
- transcript-derived events when available
- commit/work packet references

## Outputs

- WorkWindow provenance
- UserIntent, AgentAction, ToolObservation candidates
- ExternalAmoRef nodes
- IMPORTED_FROM_AMO edges

## Rules

- Preserve AMO refs for audit.
- Derive harness IDs from repo content and normalized identity.
- Do not require source-machine absolute paths.
- Missing transcripts degrade to available raw evidence instead of blocking deterministic commit facts.

## Replay Behavior

The adapter must be replayable. Re-importing the same AMO evidence creates the same harness provenance IDs or no-ops through idempotency.
