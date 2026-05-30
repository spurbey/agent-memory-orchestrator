# Extensions

Stable extension contracts live in `extensions/contracts` and are safe to import from tracked code.

Private or experimental algorithms should stay outside the tracked production package until they are promoted. The repo `.gitignore` reserves these local drop zones:

- `.local-extensions/`
- `.private-extensions/`
- `src/agent_memory_orchestrator/extensions/local/`
- `src/agent_memory_orchestrator/extensions/private/`
- `src/agent_memory_orchestrator/extensions/experimental/`

Use those paths for local-only retrieval algorithms, graph algorithms, connector experiments, and rerankers that should not ship in the public package yet.
