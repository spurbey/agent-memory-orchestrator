# AMO Peer Rooms over Tailscale

Peer rooms are temporary AMO investigation rooms created when a local memory answer is not enough. Tailscale provides the private device-to-device network; AMO owns the room state, context window, policy, and transcript.

## One-Time Setup

Each participant installs Tailscale, joins the same tailnet, and enables an AMO peer node:

```bash
amo-cli peer init --node-id sumit-zenbook --display-name "Sumit Zenbook"
amo-cli peer serve --host 0.0.0.0 --port 8787
```

On another device, add the peer by its Tailscale address:

```bash
amo-cli peer add --node-id poco-f1 --base-url http://100.76.18.75:8787 --capability graph_retrieval
```

## Open A Room

Create a local investigation room and invite one or more configured peers:

```bash
amo-cli peer open-room --topic "why did graph_service.py change?" --peer poco-f1
```

This creates:

```text
AMO_HOME/.peer/rooms/<room_id>/room.md
AMO_HOME/.peer/rooms/<room_id>/rolling_summary.md
AMO_HOME/.peer/rooms/<room_id>/transcript.jsonl
```

## Context Model

The LLM context window is assembled from three layers:

1. `room.md`: topic, initiator, participants, purpose, and sharing boundary.
2. `rolling_summary.md`: initiator-owned summary of the room discussion.
3. Recent exchanges: peers see the last two initiator-to-peer exchanges; the initiator sees the last three room conversations.

The peer config is not sent directly to the LLM. AMO projects only the safe sharing boundary into the room context.

## Peer Listener Endpoints

The peer listener exposes direct JSON endpoints:

```text
GET  /peer/health
GET  /peer/capabilities
GET  /peer/rooms
GET  /peer/rooms/{room_id}
POST /peer/rooms/invite
POST /peer/messages
```

Tailscale handles private reachability. AMO still enforces local trust policy before accepting room invites.
