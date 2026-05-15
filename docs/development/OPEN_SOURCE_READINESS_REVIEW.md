# Open Source Readiness Review

Last reviewed: 2026-05-15

## Verdict

The repository is close to public-ready for an early technical release. The core source tree, packaging metadata, local-first security posture, tests, and installer wrapper are coherent. The remaining work is release-process hardening, not a redesign.

## Current Strengths

- Clear local-first positioning in `README.md`, `SECURITY.md`, and architecture docs.
- Capture-only hook boundary is documented and covered by tests.
- Runtime state, evidence, Kuzu stores, SQLite files, exports, logs, and `.tmp` artifacts are ignored.
- Python package and npm installer wrapper have matching versions.
- Graph, retrieval, evidence, install, MCP, web, and integration modules are separated into package areas.
- Compatibility shims preserve older import paths while the public layout is being cleaned up.
- GitHub issue templates, pull request template, CI workflow, Dependabot config, and code of conduct are present.

## Release Risks To Track

- `README.md` is useful but long. Before a wider launch, keep the top-level README focused on install, first successful run, architecture summary, and links to deeper docs.
- The detailed `docs/reasoning_graph/` material is intentionally deep. Keep it, but do not make it the first path for new users.
- `python -m build` requires the `build` package. It is now part of the dev extra, but local release checks must run from an environment installed with `pip install -e ".[dev]"`.
- GitHub branch protection, repository rules, and security advisory settings cannot be verified locally. Configure them after publishing.
- Ignored generated folders such as `__pycache__/` and `*.egg-info/` may exist locally; they are not release artifacts and should remain untracked.

## Minimum Public Release Gate

Run these from the repository root:

```bash
pip install -e ".[dev]"
ruff check src tests
python -m pytest -q
python -m build
python -m twine check dist/*
```

Run these from `npm/agent-memory-orchestrator-cli`:

```bash
npm run check
npm pack --dry-run
```

Before making the repository public, verify:

- no real secrets in tracked files or history
- no generated metadata or runtime state tracked by Git
- `SECURITY.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, issue templates, PR template, CI, and Dependabot config are present
- repository security advisories are enabled
- branch protection requires CI before merge
