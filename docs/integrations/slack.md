# Slack Connector

AMO can run a local Slack Socket Mode connector. It captures selected sent Slack messages as evidence and can answer bot mentions using local GraphRAG retrieval.

## What It Does

- Captures relevant sent Slack messages into AMO evidence.
- Groups messages by team, channel, and thread.
- Replies only when the bot is explicitly mentioned in `answer` mode.
- Finalizes Slack sessions so `graph-drain` can process them.
- Keeps tokens out of the repository.

## What It Does Not Do

- It does not capture unsent typing drafts.
- It does not expose localhost to the internet.
- It does not store tokens in Git.

## Setup

Install optional Slack runtime:

```bash
npx -y agent-memory-orchestrator-cli -- install --with-slack --target codex --preset cpu-balanced --qwen-model qwen3.5:9b
```

Print a one-click Slack app setup URL:

```powershell
$env:AMO_HOME="$env:USERPROFILE\.agent-memory-orchestrator"
amo-cli slack setup-link
```

Manual manifest fallback:

```powershell
amo-cli slack manifest --out .\slack-app-manifest.json
```

After creating the Slack app, run the local wizard:

```powershell
amo-cli slack setup-wizard
```

Run the connector:

```bash
amo-cli slack run --reply-mode answer
```

## Finalize a Slack Session

```bash
amo-cli slack finalize-session --session-id "slack:T123:C123:1710000000.000100"
amo-cli graph-drain --session-id "slack:T123:C123:1710000000.000100" --limit 100
```

## Token Handling

The wizard stores tokens locally under:

```text
AMO_HOME/.secrets/slack.json
```

Use environment variables if you do not want AMO to save tokens:

```powershell
$env:AMO_SLACK_APP_TOKEN="xapp-..."
$env:AMO_SLACK_BOT_TOKEN="xoxb-..."
amo-cli slack setup --team-id T123 --bot-user-id B123 --capture-user-id U123 --skip-token-validation
```
