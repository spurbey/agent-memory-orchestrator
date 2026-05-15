# Public Release Checklist

Use this checklist before making the AMO repository public.

## Required

- [ ] Rotate any token that was pasted into chat, IDE context, or local env files.
- [ ] Verify `.env.example` contains placeholders only.
- [ ] Run a secret scan over tracked files and history.
- [ ] Confirm runtime folders are ignored and untracked:
  - `.data/`
  - `.evidence/`
  - `.graph/`
  - `.tmp/`
  - `.amo-spool/`
  - `exports/`
  - `_logs/`
  - `pytest-cache-files-*/`
- [ ] Run `ruff check src tests`.
- [ ] Run `python -m pytest -q`.
- [ ] Run `npm run check` in `npm/agent-memory-orchestrator-cli`.
- [ ] Run `npm pack --dry-run` in `npm/agent-memory-orchestrator-cli`.
- [ ] Run `python -m build` and `python -m twine check dist/*` before publishing to PyPI.
- [ ] Build/package check includes `web/*.html`, `web/*.css`, and `web/*.js`.
- [ ] Confirm Python and npm package versions match.
- [ ] Run install dry-run with the public quickstart command.
- [ ] Confirm generated packaging metadata is untracked (`*.egg-info/`, `build/`, `dist/`).
- [ ] Add or verify `SECURITY.md`.
- [ ] Add or verify `CONTRIBUTING.md`.
- [ ] Add or verify `CODE_OF_CONDUCT.md`.
- [ ] Add or verify `.github/PULL_REQUEST_TEMPLATE.md`.
- [ ] Add or verify `.github/ISSUE_TEMPLATE/`.
- [ ] Add or verify `.github/workflows/ci.yml`.
- [ ] Add or verify `.github/dependabot.yml`.

## Recommended

- [ ] Keep `README.md` focused on install, architecture, and first successful run.
- [ ] Move detailed operational flows into `docs/runbooks/`.
- [ ] Keep connector docs under `docs/connectors/`.
- [ ] Keep generated smoke artifacts in `.tmp/` only.
- [ ] Avoid committing local Kuzu, SQLite, evidence JSONL, or model cache artifacts.
- [ ] Run an OpenSSF Scorecard check after the GitHub repository is public.
- [ ] Add release notes for user-visible CLI, MCP, and graph schema changes.
