# Legacy Public API Tests

These tests cover public compatibility APIs that are still supported but are not
where the production reasoning-memory pipeline is implemented.

Covered roots:

- `agent_memory_orchestrator.memory`
- `agent_memory_orchestrator.retrieval`
- `agent_memory_orchestrator.orchestration`

Production reasoning memory should prefer `domain/`, `application/`,
`infrastructure/`, and `runtime/` boundaries. Do not add new production pipeline
coverage here.