# Codex Proxy Spike

## Purpose

Prove whether AMO can deliver `rank_tool_hits` automatically by routing Codex
provider traffic through a local proxy, then rewriting eligible tool-output
items before the model sees them.

This is a delivery spike. It must not own ranking logic, graph traversal,
embedding search, or semantic fact review.

## Headroom-Derived Shape

The required shape is:

```text
wrapper
-> local proxy
-> provider request mutation
-> upstream forwarding
-> streamed response passthrough
```

The wrapper owns Codex configuration. The proxy owns wire compatibility. The
semantic harness owns ranking.

## Wrapper Responsibilities

```text
find active CODEX_HOME / config.toml
snapshot config.toml before first mutation
inject marker-delimited AMO provider block
set model_provider to AMO provider
set openai_base_url to http://127.0.0.1:<port>/v1
declare supports_websockets = true
preserve existing user keys via backup or reversible comments
unwrap restores original config byte-for-byte when possible
```

The wrapper must be idempotent and reversible. If it cannot safely update the
config, it must fail before launching Codex.

## Proxy Responsibilities

```text
accept HTTP /v1/responses
accept WebSocket /v1/responses
forward auth and provider headers
route ChatGPT/OAuth auth to the Codex backend path
route API-key auth to the OpenAI Responses path
preserve streaming response frames/chunks
inspect request-side input/tool-output items
mutate only eligible tool outputs
fail open on AMO errors
```

The first canary is forwarding-only. No ranking mutation is allowed until a real
Codex prompt succeeds through the proxy.

## Tool Output Mutation

Eligible request-side item types:

```text
local_shell_call_output
apply_patch_call_output
function_call_output
custom_tool_call_output
```

First mutation target:

```text
local_shell_call_output containing rg/grep-style file:line output
```

Mutation output:

```text
AMO_RANKED_TOOL_HITS
<ranked file/line/symbol groups>

RAW_OUTPUT_REF sha256:<hash>
RAW_OUTPUT_EXCERPT
<bounded excerpt>
```

The raw output must be written before mutation. If raw storage fails, forward
the original request unchanged.

## Ranker Input In Proxy Mode

The proxy builds a `rank_tool_hits` request from:

```text
raw rg/grep output
latest captured UserPromptSubmit text for this session
current request goal when extractable
known anchors from recent tool calls and session state
already-seen files from prior tool-output items
repo_id from launch context or project mapping
```

The ranker then performs candidate-local similarity against projection docs
attached to the files/symbols returned by the raw tool output.

## Spike Phases

```text
1. Config canary:
   snapshot/inject/unwrap config without launching Codex.

2. Forward canary:
   launch Codex through AMO proxy and complete one harmless prompt.

3. Wire logging:
   log request item types and tool-output shapes without mutation.

4. Raw-ref canary:
   store raw rg output by sha256 and forward request unchanged.

5. Ranked-first canary:
   prepend AMO_RANKED_TOOL_HITS for rg/grep outputs only.

6. Eval:
   compare Codex raw rg vs Codex proxy-ranked rg on real tasks.
```

## Pass Criteria

```text
config unwrap restores original state
HTTP or WebSocket path used by local Codex is identified
auth succeeds through proxy
streaming response remains interactive
tool-output item shapes are parsed deterministically
raw_ref recovery works
ranked-first mutation is visible to the model
ranked output beats raw rg baseline on files opened or time-to-right-file
```

## Stop Conditions

```text
Codex cannot route through local proxy in current auth mode
WebSocket transport cannot be forwarded reliably
auth headers cannot be preserved
proxy mutation changes tool call ordering or call ids
ranked output misleads more often than raw rg
raw output recovery is missing
```

If any stop condition hits, fall back to MCP or a local command shim for the
ranker eval.
