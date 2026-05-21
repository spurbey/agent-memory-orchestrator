# agent-memory-orchestrator-cli

Thin `npx` installer wrapper for Agent Memory Orchestrator.

It installs the Python AMO runtime with `pipx`, writes local config, configures Claude/Codex hooks and MCP entries, and can initialize local stores.

## Install

```bash
npx agent-memory-orchestrator-cli -- install --target codex --preset cpu-balanced --qwen-model qwen3.5:9b
```

The `--` after the package name is intentional. It prevents npm/npx from consuming AMO flags such as `--target`.

Install for Claude and Codex:

```bash
npx agent-memory-orchestrator-cli -- install --target all --preset cpu-balanced --qwen-model qwen3.5:9b
```

If `npx` resolves `agent-memory-orchestrator-cli@0.1.1`, that registry package is too old for this command shape. Publish/use `0.1.2` or newer.

On a fresh device, install initializes the empty V2 production marker automatically. The
`v2-reset-production` command is only for an existing AMO home with old pre-V2 graph/retrieval
data that must be backed up and cleaned explicitly.

## Common Options

| Option | Meaning |
| --- | --- |
| `--target codex|claude|all` | Agent configs to patch |
| `--preset cpu-light|cpu-balanced|gpu-quality` | Local model profile |
| `--qwen-model <model>` | Ollama Qwen model written to config |
| `--with-models` | Install embedding/vector extras |
| `--with-slack` | Install Slack Socket Mode extras |
| `--download-models` | Intentionally cache selected models once |
| `--dry-run` | Show planned changes only |
| `--yes` | Apply without confirmation |
| `--skip-init-db` | Skip SQLite initialization |

## Diagnostics

```bash
npx agent-memory-orchestrator-cli -- doctor --target codex
amo-cli doctor --target codex
```

## Uninstall Managed Entries

```bash
amo-cli uninstall --target all
```

## Optional Slack Runtime

```bash
npx agent-memory-orchestrator-cli -- install --target codex --preset cpu-balanced --qwen-model qwen3.5:9b --with-slack
amo-cli slack setup-wizard
amo-cli slack run --reply-mode answer
```

Full documentation: https://github.com/spurbey/agent-memory-orchestrator#readme
