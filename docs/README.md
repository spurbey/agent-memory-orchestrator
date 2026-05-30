# Documentation

This is the documentation map for Agent Memory Orchestrator.

## Start Here

| Need | Read |
| --- | --- |
| Install and run AMO | [Root README](../README.md) |
| Understand the reasoning graph | [Reasoning Graph README](./reasoning_graph/README.md) |
| Develop locally | [Local development](./setup/local-development.md) |
| Configure local models | [Local models](./setup/local-models.md) |
| Test retrieval quality | [Retrieval pipeline](./operations/retrieval.md) |
| Build reusable skills from checkpoints | [Skill checkpoint pipeline](./skill_checkpoint/README.md) |
| Connect Slack | [Slack connector](./integrations/slack.md) |
| Understand repo layout | [Repository layout](./development/REPO_LAYOUT.md) |

## Product Shape

AMO is local-first. Hooks capture evidence, the daemon owns graph work, Kuzu stores graph truth, SQLite stores retrieval/index ledgers, and FAISS is a rebuildable vector cache.

Production source of truth:

- [Reasoning Graph overview](./reasoning_graph/README.md)

## Operational Guides

- [Local development](./setup/local-development.md)
- [Local models](./setup/local-models.md)
- [Retrieval pipeline](./operations/retrieval.md)
- [Skill checkpoint pipeline](./skill_checkpoint/README.md)
- [Slack connector](./integrations/slack.md)

## Development and Release

- [Open source readiness review](./development/OPEN_SOURCE_READINESS_REVIEW.md)
- [Public release checklist](./development/PUBLIC_RELEASE_CHECKLIST.md)
- [Repository layout](./development/REPO_LAYOUT.md)

## Historical Material

Documents not linked from this map are background material. The production docs above are the current product path.
