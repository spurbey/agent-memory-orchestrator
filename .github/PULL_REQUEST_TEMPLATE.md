## Summary

Describe the change in one or two sentences.

## Validation

- [ ] `python -m pytest -q`
- [ ] `ruff check src tests`
- [ ] `npm run check` in `npm/agent-memory-orchestrator-cli` when installer files change
- [ ] `npm pack --dry-run` in `npm/agent-memory-orchestrator-cli` when packaging files change

## Safety

- [ ] No secrets, local evidence, transcripts, Kuzu stores, SQLite databases, or `.tmp` artifacts are committed.
- [ ] Hook behavior remains capture-only and fail-open.
- [ ] Retrieval remains explicit through CLI/MCP/UI, not automatic prompt injection.
- [ ] Public docs were updated if commands, architecture, or user-visible behavior changed.

## Notes

Add migration notes, follow-up work, or screenshots when useful.
