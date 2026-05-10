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
- [ ] Build/package check includes `web/*.html`, `web/*.css`, and `web/*.js`.
- [ ] Confirm Python and npm package versions match.
- [ ] Run install dry-run with the public quickstart command.
- [ ] Add or verify `SECURITY.md`.
- [ ] Add or verify `CONTRIBUTING.md`.

## Recommended

- [ ] Keep `README.md` focused on install, architecture, and first successful run.
- [ ] Move detailed operational flows into `docs/runbooks/`.
- [ ] Keep connector docs under `docs/connectors/`.
- [ ] Keep generated smoke artifacts in `.tmp/` only.
- [ ] Avoid committing local Kuzu, SQLite, evidence JSONL, or model cache artifacts.
