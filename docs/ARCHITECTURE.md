# Architecture

This file is a short index. The canonical design is:

- [FINAL_DESIGN_V1.md](./FINAL_DESIGN_V1.md)

Execution tracking is maintained in:

- [IMPLEMENTATION_TRACKER.md](./IMPLEMENTATION_TRACKER.md)

## Summary

- Local-first persistent memory + orchestration.
- Shared MCP surface for both Claude and Codex.
- Deterministic review loop: `draft -> review -> revise -> ready_for_user -> approved/rejected`.
- Local DB is authoritative source of truth.
