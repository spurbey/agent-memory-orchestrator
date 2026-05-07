# agent-memory-orchestrator-cli

`npx` installer wrapper for Agent Memory Orchestrator.

## Usage

```bash
npx agent-memory-orchestrator-cli install
```

Options:

- `--from <pip_spec>`: install from a custom git/pip spec
- `--target codex|claude|all`: choose which agent configs to patch
- `--preset cpu-light|cpu-balanced|gpu-quality`: choose local model profile
- `--download-models`: intentionally download/cache selected models once
- `--dry-run`: show planned config changes without writing
- `--yes`: apply without the interactive confirmation prompt
- `--skip-init-db`: skip AMO SQLite initialization

Diagnostics:

```bash
npx agent-memory-orchestrator-cli doctor
```

Uninstall AMO-managed config entries:

```bash
amo-cli uninstall --target all
```
