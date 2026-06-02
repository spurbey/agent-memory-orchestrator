# AMO Peer Rooms over Tailscale

> Status: direct HTTP over Tailscale was the first test transport. The replacement direction is the embedded libp2p sidecar documented in `docs/operations/peer-network-libp2p.md`. Keep this file only as historical setup context for the Tailscale experiment.

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

For app-level message authentication over Tailscale, set the same shared secret on both devices and store only the environment variable name in AMO config:

```bash
$env:AMO_PEER_POCO_SECRET="<shared-secret>"
amo-cli peer add --node-id poco-f1 --base-url http://100.76.18.75:8787 --capability graph_retrieval --shared-secret-env AMO_PEER_POCO_SECRET
```

If a peer has `shared_secret_env` configured, unsigned invites/messages from that peer are rejected. The secret itself is not written to `peers.json`.

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

Append a local/manual message during smoke testing:

```bash
amo-cli peer append-message --room-id <room_id> --from-node-id sumit-zenbook --to-node-id poco-f1 --content "Can you check your graph memory?"
```

Build the exact three-layer prompt context AMO would hand to an LLM worker:

```bash
amo-cli peer context --room-id <room_id> --viewer-node-id poco-f1
```

Update the initiator-owned rolling summary:

```bash
amo-cli peer update-summary --room-id <room_id> --summary "Current understanding: poco-f1 found packet WP0030."
```

## Context Model

The LLM context window is assembled from three layers:

1. `room.md`: topic, initiator, participants, purpose, and sharing boundary.
2. `rolling_summary.md`: initiator-owned summary of the room discussion.
3. Recent exchanges: peers see the last two initiator-to-peer exchanges; the initiator sees the last three room conversations.

The peer config is not sent directly to the LLM. AMO projects only the safe sharing boundary into the room context.

The old direct-HTTP experiment used this rough layout:

```text
src/agent_memory_orchestrator/peer/
  models.py      # peer config, node roster, and share-boundary settings
  auth.py        # optional HMAC envelopes for signed peer messages
  policy.py      # auto-join/trust decisions and safe LLM policy projection
  protocol.py    # normalized peer-room message records
  context.py     # three-layer context-pack assembly
  store.py       # local room files: room.md, rolling_summary.md, transcript.jsonl
  service.py     # orchestration API used by CLI/server
  server.py      # direct HTTP listener for Tailscale/private transport
```

Current production peer architecture is the libp2p sidecar path documented in
[`peer-network-libp2p.md`](./peer-network-libp2p.md). Current source ownership
is documented in [`../ARCHITECTURE_TREE.md`](../ARCHITECTURE_TREE.md); do not use
the historical `server.py` path for new work.

## Peer Listener Endpoints

The peer listener exposes direct JSON endpoints:

```text
GET  /peer/health
GET  /peer/capabilities
GET  /peer/rooms
GET  /peer/rooms/{room_id}
GET  /peer/rooms/{room_id}/context?viewer=<node_id>
POST /peer/rooms/invite
POST /peer/messages
POST /peer/rooms/{room_id}/summary
```

Tailscale handles private reachability. AMO still enforces local trust policy before accepting room invites.
