# agent-memory-orchestrator-cli

Thin `npx` installer wrapper for Agent Memory Orchestrator.

It installs the Python AMO runtime with `pipx`, writes local config, configures Claude/Codex hooks and MCP entries, and can initialize local stores.

## Install

```bash
npx -y agent-memory-orchestrator-cli -- install --target codex --preset cpu-balanced --qwen-model qwen3:1.7b
```

The `--` after the package name is intentional. It prevents npm/npx from consuming AMO flags such as `--target`.

Install for Claude and Codex:

```bash
npx -y agent-memory-orchestrator-cli -- install --target all --preset cpu-balanced --qwen-model qwen3:1.7b
```

If `npx` resolves `agent-memory-orchestrator-cli@0.1.1`, that registry package is too old for this command shape. Publish/use `0.1.6` or newer for peer sidecar install support.

The wrapper automatically skips Python versions that are too new for AMO's Kuzu
dependency and asks `pipx` to create the AMO app with Python 3.10, 3.11, 3.12,
or 3.13. This avoids the common macOS ARM failure where `python3` points to
Python 3.14 and pip tries to build Kuzu from source. If automatic discovery
cannot find the right interpreter, install Python 3.13 and rerun the same
command.

For unusual machines, you can override the interpreter explicitly:

```bash
npx -y agent-memory-orchestrator-cli -- install --pipx-python /opt/homebrew/bin/python3.13 --target codex
```

On a fresh device, install initializes the empty production marker automatically. The
`reset-production` command is only for an existing AMO home with graph/retrieval
data that must be backed up and cleaned explicitly.

Install is per user/device, not per repository. AMO hooks capture Codex/Claude
sessions from any working directory. Closed-session production jobs resolve the actual
Git repository, store a durable `repo_id`, and keep central memory, active
GraphView, retrieval docs, embeddings, and dashboard views scoped by that repo.
If a user works in another repo later, AMO creates separate repo-scoped memory
from the same install.

## Common Options

| Option | Meaning |
| --- | --- |
| `--target codex|claude|all` | Agent configs to patch |
| `--pipx-python <path>` | Optional override for the Python used by pipx |
| `--preset cpu-light|cpu-balanced|gpu-quality` | Local model profile |
| `--qwen-model <model>` | Ollama Qwen model written to config |
| `--with-models` | Install embedding/vector extras |
| `--with-slack` | Install Slack Socket Mode extras |
| `--with-peer` | Install and verify the signed Windows/macOS peer networking sidecar |
| `--download-models` | Intentionally cache selected models once |
| `--dry-run` | Show planned changes only |
| `--yes` | Apply without confirmation |
| `--skip-init-db` | Skip SQLite initialization |

## Diagnostics

```bash
npx -y agent-memory-orchestrator-cli -- doctor --target codex
amo-cli doctor --target codex
```

If Windows reports `No module named 'agent_memory_orchestrator.app.cli'`,
the terminal is still finding an old pip-installed `amo-cli.exe` shim. Remove
the stale package and rerun the `npx` installer:

```powershell
py -3.11 -m pip uninstall -y agent-memory-orchestrator agent-memory-orchestrator-cli
pipx uninstall agent-memory-orchestrator
npx -y agent-memory-orchestrator-cli@latest -- install --from "git+https://github.com/spurbey/agent-memory-orchestrator.git@main" --target codex --preset cpu-balanced --qwen-model qwen3:1.7b --yes --force
```

## Uninstall Managed Entries

```bash
amo-cli uninstall --target all
```

## Optional Slack Runtime

```bash
npx -y agent-memory-orchestrator-cli -- install --target codex --preset cpu-balanced --qwen-model qwen3:1.7b --with-slack
amo-cli slack setup-wizard
amo-cli slack run --reply-mode answer
```

Full documentation: https://github.com/spurbey/agent-memory-orchestrator#readme
