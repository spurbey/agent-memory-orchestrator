# MCP To Proxy Delivery Plan

## Purpose

Separate product intelligence from delivery. MCP proves usefulness first; proxy
delivery comes after quality gates pass.

## MCP Phase

MCP is explicit:

```text
agent decides to call amo_harness_query
agent sends mode, goal, anchors, questions, and budget
AMO returns mode-specific output
```

MCP is used for:

- context questions
- pre-edit checks
- relationship/history questions
- controlled evals

## Proxy Phase

Proxy is automatic delivery:

```text
Codex executes native tool
-> Codex builds next provider request with tool output
-> AMO proxy sees provider request
-> AMO rewrites eligible tool-output items
-> upstream provider receives ranked-first/raw-preserved output
```

First proxy use case:

```text
ranked-first rank_tool_hits after rg/grep output
```

The proxy does not intercept shell execution. It intercepts the next LLM
request, where Codex has already appended the tool result to the model input.

## Codex Proxy Requirements

The Codex proxy must be implemented as a separate delivery layer, not inside the
semantic harness graph/query modules.

Required wrapper behavior:

```text
snapshot Codex config before mutation
inject AMO model provider
set openai_base_url to local AMO proxy
declare supports_websockets = true
preserve/restore original config on unwrap
do not permanently rewrite user config without markers
```

Required proxy behavior:

```text
forward Authorization and provider headers
detect ChatGPT/OAuth style routing separately from API-key routing
support HTTP /v1/responses
support WebSocket /v1/responses if the active Codex version uses it
preserve streaming responses without buffering the full upstream response
mutate only request-side tool-output items
fail open to original request on AMO errors
write raw tool output to stable raw_ref before mutation
```

Eligible tool-output item kinds:

```text
local_shell_call_output
apply_patch_call_output
function_call_output
custom_tool_call_output
```

For the first slice, only `rg`/`grep`-like shell outputs are mutated.

## Ranked-First Output Shape

Initial proxy mutation should preserve recoverability:

```text
AMO_RANKED_TOOL_HITS
1. path score reasons best_lines mapped_symbols
2. path score reasons best_lines mapped_symbols

RAW_OUTPUT_REF sha256:<hash>
RAW_OUTPUT_EXCERPT
<short raw excerpt or note that raw output was too large>
```

The raw output remains stored by `raw_ref`. Ranked-only replacement is not
allowed in the first proxy slice.

## Replacement Policy

Replacement is disabled until:

- raw output has stable `raw_ref`
- lossless recovery works
- precision and mislead gates pass
- exit code and failure state are preserved
- proxy routing is stable

Initial mutation behavior is ranked-first/raw-preserved, not ranked-only. Before
that mutation canary, proxy behavior is forwarding-only plus logging.
