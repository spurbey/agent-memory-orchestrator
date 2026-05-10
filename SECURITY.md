# Security Policy

Agent Memory Orchestrator is local-first software. It stores agent evidence, graph memory, and optional connector secrets on the user's machine.

## Supported Versions

Security fixes target the latest released version.

## Reporting a Vulnerability

Open a private security advisory on GitHub or contact the maintainers before publishing details. Include:

- affected version or commit
- operating system
- install method
- reproduction steps
- whether local evidence, graph data, or connector secrets are exposed

Do not paste real tokens, private keys, or raw private evidence into public issues.

## Local Secret Handling

- Runtime state belongs under `~/.agent-memory-orchestrator`.
- Slack tokens, when saved, belong under `~/.agent-memory-orchestrator/.secrets/`.
- `.env`, `.data/`, `.evidence/`, `.graph/`, `.amo-spool/`, exports, logs, and SQLite files must stay untracked.
- Rotate any token that was pasted into chat, issue text, logs, screenshots, or local debugging output.

## Network Boundary

AMO is designed to run locally. Kuzu and SQLite are embedded. Slack Socket Mode uses an outbound WebSocket and does not require exposing localhost.
