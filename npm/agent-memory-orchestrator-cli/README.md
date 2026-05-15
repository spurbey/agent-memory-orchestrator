# agent-memory-orchestrator-cli

`npx` installer wrapper for Agent Memory Orchestrator.

## Usage

```bash
npx agent-memory-orchestrator-cli install --target codex --preset cpu-balanced --qwen-model qwen3.5:9b
```

Options:

- `--from <pip_spec>`: install from a custom git/pip spec
- `--target codex|claude|all`: choose which agent configs to patch
- `--preset cpu-light|cpu-balanced|gpu-quality`: choose local model profile
- `--qwen-model <ollama_model>`: override the Qwen model written to AMO config
- `--with-models`: install optional embedding/vector packages into the pipx app
- `--with-slack`: install optional Slack Socket Mode runtime into the pipx app
- `--with-all-extras`: install all optional runtime packages
- `--download-models`: intentionally download/cache selected models once
- `--dry-run`: show planned config changes without writing
- `--yes`: apply without the interactive confirmation prompt
- `--skip-init-db`: skip AMO SQLite initialization

Diagnostics:

```bash
npx agent-memory-orchestrator-cli doctor --target codex
```

Uninstall AMO-managed config entries:

```bash
amo-cli uninstall --target all
```

Slack is optional:

```bash
npx agent-memory-orchestrator-cli install --target codex --preset cpu-balanced --qwen-model qwen3.5:9b --with-slack
amo-cli slack setup-wizard
amo-cli slack run --reply-mode answer
```
