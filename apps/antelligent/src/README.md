# Antelligent Frontend Guide

This folder contains the TypeScript, DOM rendering, API client, and CSS for the
Antelligent floating companion. It is intentionally plain Vite plus TypeScript.
There is no React, Angular, or frontend state framework in v1.

The frontend never owns AMO logic. It renders daemon-shaped state and sends user
actions to daemon endpoints.

## File Map

```text
src/
  main.ts              Entry point. Chooses bubble or panel by URL hash.
  api/
    client.ts          REST client for /api/antelligent/*.
    events.ts          WebSocket client for /api/antelligent/events.
    types.ts           UI-facing TypeScript contracts.
  app/
    controller.ts      UI lifecycle, event binding, actions, refresh flow.
    render.ts          DOM string renderers for views and messages.
    state.ts           AppState, view names, local node helpers.
    dom.ts             escaping, formatting, query helpers.
  styles/
    tokens.css         design tokens, font import, base document behavior.
    bubble.css         floating ant icon.
    panel.css          shell, rail, retrieval, rooms, chat layout.
    timeline.css       message bubbles, composer, support rows.
```

## Boot Flow

`main.ts` decides which UI to mount:

```text
index.html#bubble -> mountBubble(root)
index.html#panel  -> new AntelligentController(root).start()
```

The two hashes match the Tauri window config in
`apps/antelligent/src-tauri/tauri.conf.json`.

Bubble mode:

- Renders only the ant bubble.
- Enables native window drag on the bubble.
- Calls Tauri command `show_panel` when clicked.

Panel mode:

- Renders the full shell.
- Locks document-level scroll so inner panes own scrolling.
- Loads status and rooms.
- Opens the WebSocket event stream.
- Refreshes room state when events arrive.

## View Model

The UI has exactly three product views:

```text
retrieval  Ask AMO memory. Opens peer room only when local confidence is low.
chat       Live room view for one selected room.
rooms      Room history for rooms created or joined by this AMO.
```

Do not add placeholder tabs for features that do not exist in backend yet. If a
future feature is not backed by daemon state, keep it out of the UI.

`state.ts` owns the minimal state:

```text
status             readiness payload from /status
rooms              room list from /rooms
selectedRoomId     active room id
messages           selected room messages
context            selected room context pack
events             recent UI event strip
lastQuery          last retrieval text
lastChatResult     result from /chat
online             WebSocket state
busy               local UI action in progress
```

## API Client

`api/client.ts` gets backend connection info by invoking the Tauri command
`backend_info`.

```text
backend_info -> { baseUrl, token }
```

Then all HTTP requests are sent to:

```text
{baseUrl}/api/antelligent/*
Authorization: Bearer {token}
```

The localStorage fallback exists for browser/Vite development only. The shipped
Tauri app should use launch config plus token file via the Rust command.

Current wrappers:

```text
status()       GET  /api/antelligent/status
rooms()        GET  /api/antelligent/rooms
messages(id)   GET  /api/antelligent/rooms/{id}/messages
context(id)    GET  /api/antelligent/rooms/{id}/context
chat(query)    POST /api/antelligent/chat
askRoom(id,q)  POST /api/antelligent/rooms/{id}/ask
continueRoom   POST /api/antelligent/rooms/{id}/continue
```

When adding a daemon endpoint, add a typed wrapper here instead of scattering
`fetch` calls through the controller.

## WebSocket Client

`api/events.ts` connects to:

```text
WS /api/antelligent/events?token=<local-ui-token>
```

The browser WebSocket API cannot set `Authorization` headers, so the token is in
the query string. Keep this endpoint localhost-only and do not log the full URL.

Events are UI refresh hints:

```text
daemon_status
worker_status
room_created
room_updated
message_appended
agent_state_updated
summary_updated
room_finalized
heartbeat
```

The event payload is not the source of truth. On meaningful events, the
controller refreshes rooms and selected room details through HTTP.

## Controller Responsibilities

`app/controller.ts` is the only place that should coordinate UI behavior.

Main responsibilities:

- Render the initial shell.
- Bind top-level buttons and view navigation.
- Submit retrieval searches through `chat(query)`.
- Select rooms and load messages/context.
- Submit follow-up room questions through `askRoom(roomId, query)`.
- Continue room reasoning through `continueRoom(roomId)`.
- Connect/reconnect WebSocket events.
- Call Tauri commands `show_panel` and `hide_panel`.
- Enable draggable bubble and draggable panel window.

Keep controller logic imperative and small. If a behavior grows large, extract a
small helper module under `app/` instead of expanding `controller.ts` into a
monolith.

## Rendering Rules

`app/render.ts` owns DOM string generation. It should stay deterministic and
side-effect free except for updating the target DOM.

Important conventions:

- Escape all user/daemon text with `escapeHtml`.
- Local device/agent messages render on the right when `from_node_id` equals the
  current local node id from `status.peer.node.node_id`.
- Other peer messages render on the left.
- The live room view must provide a way back to room history.
- Room list and message list should scroll inside their pane, not by scrolling
  the full transparent Tauri window.
- The context drawer shows room brief, rolling summary, participants, final
  synthesis, and support cards only from daemon-shaped data.

## Styling Ownership

Use the existing CSS split:

```text
tokens.css    variables, font, root/body behavior
bubble.css    ant bubble only
panel.css     shell layout, tabs, panels, room list, context drawer
timeline.css  chat messages, composer, support cards
```

Keep the visual direction simple, robotic, and agentic. Avoid generic dashboard
cards that look unrelated to the product. Avoid adding large UI widgets unless
the backend can actually populate them.

## Common Update Recipes

### Add a field to status

1. Add it in `runtime/daemon/antelligent_supervisor.py`.
2. Update `api/types.ts`.
3. Render it in `render.ts` or `readinessSummary`.

### Add a new event

1. Emit it from `runtime/daemon/antelligent_events.py`.
2. Add the type expectation in `api/types.ts` if it needs structure.
3. Handle it in `controller.ts:onEvent`.
4. Prefer refreshing canonical HTTP state instead of trusting event payloads.

### Add a new room action

1. Implement the action in `PeerAgentService`.
2. Route it through `runtime/daemon/routes/antelligent.py`.
3. Add `api/client.ts` wrapper.
4. Add controller binding.
5. Render result in `render.ts`.

### Add complex harness visualization

1. Put harness execution and persistence under `src/agent_memory_orchestrator`.
2. Add a daemon endpoint that returns a safe, bounded UI shape.
3. Add a small frontend view only after the backend shape exists.

## Debugging

Offline UI:

- `backend_info` failed or token/base URL is wrong.
- Daemon is not reachable on the configured URL.
- `/api/antelligent/status` rejects the token.
- Rust supervisor could not spawn daemon from launch config.

Rooms not updating:

- WebSocket may be disconnected, but manual refresh should still work.
- Check `GET /api/antelligent/rooms`.
- Check `peer-agent watch` and room files through daemon routes.

Blank panel:

- Check whether Tauri opened `index.html#panel`.
- Check Vite build output with `npm run build`.
- Check browser console in Tauri dev tools if available.
- Check recent changes in `renderShell`, `renderView`, and CSS overflow rules.

Unexpected full-window scrolling:

- Check `tokens.css` root/body overflow.
- Check `.stage`, `.view-root`, `.chat-layout`, `.rooms-list-large`, and
  `.agent-conversation` height/min-height/overflow rules.

## Guardrails

- Do not read room files directly from TypeScript.
- Do not call OpenAI, Anthropic, Ollama, or provider APIs from TypeScript.
- Do not implement peer protocol logic here.
- Do not store provider keys or peer secrets in localStorage.
- Do not add fake UI states that are not backed by daemon state.
