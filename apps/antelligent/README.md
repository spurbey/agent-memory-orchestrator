# Antelligent Developer Guide

Antelligent is the floating desktop companion for Agent Memory Orchestrator.
It gives the user a visible AMO surface: memory search, live peer rooms, room
history, status, and agent activity. It is intentionally a UI and control shell.

Antelligent does not own memory, retrieval, peer networking, provider API calls,
or room files. Those stay in the Python AMO daemon, Python peer-agent worker, and
Go peer-netd sidecar.

## System Boundary

```text
User
  |
  v
Antelligent desktop shell
  |  apps/antelligent/src       TypeScript UI, REST/WebSocket client, rendering
  |  apps/antelligent/src-tauri Rust windows, tray, PID, config, daemon spawn
  v
AMO daemon
  |  runtime/daemon/routes/antelligent.py       protected local API
  |  runtime/daemon/antelligent_events.py       UI event stream
  |  runtime/daemon/antelligent_supervisor.py   readiness view
  v
Peer-agent service
  |  peer/agent/service.py      ask/watch/room orchestration
  |  peer/agent/state.py        agent_state.json, idempotency, final state
  |  peer/agent/llm.py          local Ollama, initiator-side API fallback
  |  peer/agent/quality.py      local answer quality gate
  v
Peer room state and transport
  |  peer/store.py              room.json, room.md, rolling_summary.md, transcript.jsonl
  |  domain/peer/rooms.py       context pack layers
  |  peer/netd_service.py       Python netd lifecycle bridge
  |  peer-netd/                 Go libp2p sidecar, transport only
```

## Product Responsibilities

Antelligent owns:

- Floating bubble and panel presentation.
- Tray restore and desktop process lifecycle.
- Local daemon reachability checks and daemon spawn attempt from launch config.
- UI calls to daemon endpoints.
- UI-only event rendering and room visualization.

AMO daemon owns:

- Authentication for local Antelligent API access.
- Shaping room data for UI consumption.
- WebSocket event snapshots and deltas.
- Calling `PeerAgentService` for chat, room ask, continuation, and summary.
- Hiding raw room files and sensitive configuration from the desktop app.

Peer-agent owns:

- Local-first memory retrieval.
- Low-confidence peer room creation.
- Context request and response handling.
- Room summarization and final synthesis.
- Local Ollama use on peers and initiator-side provider fallback.

peer-netd owns:

- libp2p transport, relay/rendezvous, send/receive envelope movement.
- No memory, no LLM, no summarization, no provider API.

## Important Source Map

```text
apps/antelligent/
  index.html                 Vite entry host.
  package.json               frontend build scripts and Tauri CLI dependency.
  vite.config.ts             Vite config.
  src/                       TypeScript UI. See src/README.md.
  src-tauri/                 Tauri shell. See src-tauri/README.md.

src/agent_memory_orchestrator/runtime/daemon/
  routes/antelligent.py      local HTTP and WebSocket route handlers.
  antelligent_auth.py        bearer token path and token validation.
  antelligent_events.py      WebSocket snapshot/delta loop.
  antelligent_supervisor.py  daemon, peer, netd, worker, LLM readiness payload.

src/agent_memory_orchestrator/runtime/antelligent/
  artifacts.py               release manifest and artifact selection/download.
  install.py                 atomic app artifact install/update/uninstall.
  launch_config.py           writes .ui/antelligent.launch.json.
  paths.py                   canonical install and metadata paths.
  process.py                 start/stop/status with PID validation.
  startup.py                 Windows Run key and macOS LaunchAgent.
  doctor.py                  operator-facing diagnosis.

src/agent_memory_orchestrator/runtime/cli/commands/antelligent.py
  amo-cli antelligent install/start/stop/status/doctor/startup commands.

src/agent_memory_orchestrator/runtime/cli/commands/install.py
  AMO install integration for --with-antelligent.

npm/agent-memory-orchestrator-cli/bin/cli.js
  npm bridge that forwards install flags to the Python CLI.

.github/workflows/antelligent-release.yml
  CI release workflow for portable Antelligent artifacts.

scripts/package_antelligent_artifact.py
  Packages Tauri outputs into platform artifacts and manifest entries.
```

## Local API Contract

The UI only talks to protected localhost daemon endpoints.

```text
GET  /api/antelligent/status
GET  /api/antelligent/rooms
GET  /api/antelligent/rooms/{room_id}
GET  /api/antelligent/rooms/{room_id}/messages
GET  /api/antelligent/rooms/{room_id}/context
POST /api/antelligent/chat
POST /api/antelligent/rooms/{room_id}/ask
POST /api/antelligent/rooms/{room_id}/continue
POST /api/antelligent/rooms/{room_id}/summarize
WS   /api/antelligent/events
```

All HTTP calls require:

```text
Authorization: Bearer <local-ui-token>
```

The WebSocket currently passes the same local token as a query parameter because
browser WebSocket constructors cannot set arbitrary headers. Keep this endpoint
local-only. Never send provider API keys, peer secrets, raw room files, or raw
memory evidence through Antelligent.

## Runtime Data Flow

### Memory Search

```text
Retrieval tab submit
  -> src/api/client.ts POST /api/antelligent/chat
  -> routes/antelligent.py
  -> PeerAgentService.ask()
  -> local V2 retrieval
  -> if strong: local_only result
  -> if weak: create peer room, send context_request, wait/finalize if possible
  -> UI receives ChatResult and optionally opens the live room
```

### Live Room

```text
Room selected in UI
  -> GET /rooms/{room_id}/messages
  -> PeerAgentService.messages()
  -> peer room transcript.jsonl shaped by daemon

Room context drawer
  -> GET /rooms/{room_id}/context
  -> PeerAgentService.context()
  -> domain.peer.rooms context pack
  -> room brief, rolling summary, roster, group messages, pairwise messages, policy view

Ask Room
  -> POST /rooms/{room_id}/ask
  -> PeerAgentService.ask_room()
  -> tagged peers receive context_request through peer-netd
```

### Real-time Events

```text
UI WebSocket
  -> /api/antelligent/events
  -> antelligent_events.py
  -> periodic room/status snapshots
  -> emitted UI events: daemon_status, worker_status, room_created,
     room_updated, message_appended, agent_state_updated, summary_updated,
     room_finalized, heartbeat
```

The event stream is for UI refresh signals. It is not the source of truth. When
an event arrives, the UI should refresh rooms/messages/context from the HTTP API.

## Install And Release Path

Antelligent is installed as a portable app artifact, not as a dev command.

User-facing install path:

```powershell
npx -y agent-memory-orchestrator-cli -- install --target all --with-antelligent --antelligent-startup
```

Daily commands:

```powershell
amo-cli antelligent start
amo-cli antelligent stop
amo-cli antelligent status
amo-cli antelligent doctor
```

Developer commands from this folder:

```powershell
npm install
npm run build
npm run tauri -- dev
```

Release artifacts are built by GitHub Actions and consumed by
`runtime/antelligent/artifacts.py`. The installer verifies SHA256 before
installing. Internal v1 uses unsigned portable artifacts; public distribution
will need signing/notarization later to reduce OS trust prompts.

## Debugging Checklist

If the panel says offline:

- Check `amo-cli antelligent doctor`.
- Check `AMO_HOME/.ui/antelligent.launch.json`.
- Check `AMO_HOME/.ui/antelligent.token`.
- Check `GET /api/antelligent/status` through the daemon.
- Check `src-tauri/src/supervisor.rs` only if the daemon should auto-start.

If rooms are stale:

- Confirm `peer-agent watch` is running or startup is configured.
- Check `src/agent_memory_orchestrator/peer/agent/service.py`.
- Check room files through daemon endpoints, not direct UI file reads.
- Check `/api/antelligent/events` emits `message_appended` or `room_updated`.

If peer transport fails:

- Check `amo-cli peer doctor`.
- Check `peer/netd_service.py` and `peer-netd/`.
- Keep transport debugging out of the desktop app.

If the UI is blank:

- Check `apps/antelligent/src/main.ts` mode selection by `#bubble` or `#panel`.
- Check `src-tauri/tauri.conf.json` window URLs.
- Run `npm run build`.
- For Tauri issues, run `npm run tauri -- dev` from `apps/antelligent`.

## Adding Features Safely

Add UI-only feature:

1. Add types in `src/api/types.ts` if daemon payload changes.
2. Add API wrapper in `src/api/client.ts` if a new endpoint is needed.
3. Add controller behavior in `src/app/controller.ts`.
4. Add rendering in `src/app/render.ts`.
5. Add styling in `src/styles/*.css`.

Add a new backend capability:

1. Implement the actual logic in `src/agent_memory_orchestrator/...`.
2. Shape a safe UI payload in `runtime/daemon/routes/antelligent.py`.
3. Add event notification in `runtime/daemon/antelligent_events.py` only if the
   UI needs live refresh.
4. Keep raw evidence, provider tokens, peer secrets, and filesystem details out
   of the payload.

Add a new Tauri desktop behavior:

1. Implement Rust shell logic under `src-tauri/src`.
2. Expose only small commands through `src-tauri/src/lib.rs`.
3. Call those commands from TypeScript with `@tauri-apps/api/core`.
4. Do not move AMO business logic into Rust.

Add future harness or complex agent logic:

- Backend harness logic belongs in `src/agent_memory_orchestrator/...`.
- Antelligent should receive a shaped status, event, or result through daemon
  APIs.
- The UI can visualize the harness, but must not become the harness runtime.

## Non-goals

- No provider API calls from Tauri or TypeScript.
- No direct room file reads from the desktop app.
- No peer-netd protocol logic in the app.
- No browser capture, meeting notes, OCR, or screen watching in this slice.
- No final synthesis broadcast to peers unless a future explicit product option
  adds sanitized broadcast.
