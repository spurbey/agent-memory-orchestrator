# AMO Peer Network over libp2p

This is the replacement direction for the earlier direct HTTP/Tailscale peer-room experiment. The goal is to keep the user inside AMO: install AMO once, enable peer participation once, then let AMO create rooms and contact trusted peers without asking users to configure a separate private-network product.

## Design Constraint

AMO should minimize hosted infrastructure, but it cannot pretend the public internet is easy. Most laptops and phones sit behind NAT or firewalls, so fully direct inbound dialing is not reliable. libp2p gives us the right building blocks: peer identity, multiaddrs, secure streams, hole punching, AutoNAT, and circuit relay.

The final UX should hide those details:

```text
amo-cli peer enable
amo-cli peer join <trust-group-or-invite>
```

After that, AMO should run a local peer sidecar in the background. Joining future rooms should not require user action unless policy requires approval.

## Runtime Split

```text
Python AMO
  owns memory retrieval
  owns room.md and rolling_summary.md
  owns trust policy and sharing boundaries
  owns final answer synthesis
  calls localhost sidecar API

Go amo-peer-netd
  owns libp2p host identity
  owns peer dialing and streams
  owns relay/bootstrap integration later
  verifies transport envelopes
  never reads raw AMO databases directly
```

This prevents the network layer from becoming a second memory system.

## Current File Structure

```text
peer-netd/
  cmd/amo-peer-netd/main.go
  internal/config/config.go
  internal/localapi/server.go
  internal/p2p/node.go
  internal/protocol/protocol.go
  internal/rendezvous/rendezvous.go
  internal/store/store.go

src/agent_memory_orchestrator/peer/
  cards.py
  doctor.py
  netd_client.py
  netd_runtime.py
  netd_service.py
```

`cards.py` builds and imports peer-card JSON so users do not manually copy libp2p ids and multiaddrs into commands.

`doctor.py` produces the operator readiness report for peer identity, packaged `peer-netd` source, binary/build state, sidecar health, trusted peers, and shared-secret environment variables.

`netd_client.py` is the Python bridge. It talks to `amo-peer-netd` over localhost HTTP and converts AMO peer-room messages into sidecar send requests.

`netd_runtime.py` is the managed sidecar lifecycle layer. It locates repo or packaged `peer-netd` source, builds the Go binary into `AMO_HOME/.peer/bin`, starts/stops it, writes PID/API/log state under `AMO_HOME/.peer/netd`, and refuses unsafe managed starts where the local API port is dynamic.

`netd_service.py` plans OS startup integration. It returns a Windows Scheduled Task plan or user-systemd unit plan by default, and only mutates the host when `--apply` is explicitly used.

## Managed User Flow

The intended user path is AMO-owned, not Tailscale-owned:

```powershell
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> init --node-id zenbook-amo
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> doctor
$env:AMO_PEER_NETD_SECRET="<shared-secret>"
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> enable `
  --node-id zenbook-amo `
  --api 127.0.0.1:8788 `
  --shared-secret-env AMO_PEER_NETD_SECRET `
  --require-signature
```

Operational commands:

```powershell
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> doctor --strict
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd build
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd start --node-id zenbook-amo
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd status
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd stop
```

`peer doctor` is the first diagnostic command for a new machine. It separates blocking install/config failures from normal next steps like starting the sidecar or importing peer cards.

`peer enable` is the one-command normal path. It builds the sidecar if needed, starts it, waits for `/health`, and returns the peer id/listen addresses. Packaged installs now include the Go sidecar source so users should not need a repo clone just to build `amo-peer-netd`; they still need Go on PATH until prebuilt sidecar binaries are shipped. Future install work should wire this into a background OS service, but the process state is already AMO-owned.

Delivered envelopes are persisted by default:

```text
AMO_HOME/.peer/netd/inbox.jsonl
```

If the sidecar receives a message and restarts before AMO runs `poll-netd`, the inbox reloads from this JSONL file.

Startup planning:

```powershell
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd install-service --node-id zenbook-amo
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd install-service --node-id zenbook-amo --apply
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd service-status
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd uninstall-service --apply
```

The default is a plan, not mutation. On Windows the apply path creates a per-user Scheduled Task at logon. On Linux it writes a user systemd unit and enables it.

## Room Flow Over Netd

Configure peers with peer cards where possible:

```powershell
python -m agent_memory_orchestrator.app.cli peer --amo-home <home_b> share-card --out node-b.card.json
python -m agent_memory_orchestrator.app.cli peer --amo-home <home_a> import-card --file node-b.card.json
```

Manual config still exists for smoke tests and advanced use:

```powershell
python -m agent_memory_orchestrator.app.cli peer --amo-home <home_a> add --node-id node-b --peer-id <node_b_libp2p_peer_id> --multiaddr <node_b_multiaddr>
```

Then the normal room path is:

```powershell
python -m agent_memory_orchestrator.app.cli peer --amo-home <home_a> open-room --topic "..." --peer node-b
python -m agent_memory_orchestrator.app.cli peer --amo-home <home_b> poll-netd
python -m agent_memory_orchestrator.app.cli peer --amo-home <home_b> send-message --room-id <room_id> --peer-id node-a --type context_response --content "..." --citation E0001 --confidence 0.91
python -m agent_memory_orchestrator.app.cli peer --amo-home <home_a> poll-netd
```

`poll-netd` uses the managed sidecar API URL from `AMO_HOME/.peer/netd/netd.json`, so each AMO home can run on a different local API port without extra environment variables.

## Implemented Flow

```text
peer enable starts amo-peer-netd
node prints peer_id and listen_addrs
optional LAN mDNS discovers local peers
optional rendezvous node registers peers by namespace
optional relay service lets private peers reserve /p2p-circuit addresses
AMO discovers and connects to a peer multiaddr
AMO sends signed peer_response
remote sidecar verifies envelope
remote AMO reads /messages and processes room response
```

The current implementation supports explicit multiaddr dialing, peer-card export/import, packaged sidecar source discovery, readiness diagnostics, static bootstrap dialing, LAN mDNS, managed process start/stop, persistent sidecar inbox, sidecar-backed room invites/messages, relay reservation, an AMO rendezvous stream protocol, and OS startup planning. Prebuilt sidecar binaries and background polling are still the next packaging/UX steps.

## Rendezvous Shape

```text
public or always-on AMO node runs: amo-peer-netd --rendezvous-server
peer A registers namespace: amo-team
peer B registers namespace: amo-team
peer B discovers namespace: amo-team
peer B receives peer A multiaddrs and dials directly when possible
room messages still use signed AMO envelopes over /amo/peer/1.0.0
```

The rendezvous node stores temporary peer addresses only. It does not store room transcripts, raw memory, evidence, summaries, or LLM prompts.

## Relay Shape

```text
public or always-on node runs: amo-peer-netd --relay-service
private peer runs locally and calls /relay/reserve with the relay multiaddr
private peer receives /p2p-circuit address
other peer dials that relay address
AMO opens /amo/peer/1.0.0 stream with transient relay fallback enabled
signed room message is delivered to the private peer
```

Relay nodes should be treated as transport utilities. They should be rate-limited and monitored, but they should not read AMO memory or own room state.

## Why libp2p, Not Tailscale

Tailscale solved private reachability quickly, but it makes users leave AMO and join a separate network. libp2p is a better product direction because AMO can embed the peer node as a sidecar and gradually add discovery, relay, and NAT traversal behind a stable AMO UX.

## Why Some Hosted Infra Still Exists

For real-world devices, AMO will still need lightweight public nodes:

- bootstrap/rendezvous: peers need a known place to discover each other.
- relay: private peers sometimes need a public relay path.
- monitoring: we need to know if discovery/relay nodes are unhealthy.
- abuse control: public relay capacity must be rate-limited.
- auth/trust: only trusted nodes should join rooms or request memory.

These nodes should not store memory, raw evidence, or LLM prompts. They only move encrypted/signed envelopes and help peers discover/dial each other.

## Current Validation

- Go unit/integration tests verify signed envelopes, direct libp2p delivery, and rendezvous discovery followed by delivery.
- Python tests verify the AMO localhost client can call health, bootstrap, rendezvous, send, and messages APIs.
- Python runtime tests verify managed sidecar command construction, state paths, missing-secret safety, and fixed API-port validation.
- CLI tests verify `peer netd status` uses `--amo-home`, `peer doctor` reports readiness, and `peer enable` rejects dynamic API ports before building.
- CLI tests verify libp2p peer config, peer-card export/import, inbox polling failure behavior, and startup service planning.
- Wheel install smoke verifies packaged installs contain the `peer-netd` Go source tree and `PeerNetdRuntime` can discover it outside the repo.
- Go store tests verify delivered envelopes persist to JSONL and reload after restart.
- Binary smoke starts three real sidecar processes: rendezvous, node A, and node B. A/B register, B discovers A, B sends a signed response, and A receives it.
- Binary relay smoke starts three real sidecar processes: relay, private node A, and node B. A reserves a relay slot, B dials A's `/p2p-circuit` address, B sends a signed response, and A receives it.
- Managed runtime smoke starts the sidecar through `python -m agent_memory_orchestrator.app.cli peer enable`, checks `peer netd status`, then stops it through `peer netd stop`.
- Two-node room smoke starts two sidecars with two separate AMO homes, sends `open-room` invite over libp2p, accepts it with `poll-netd`, sends a `context_response`, and ingests it on the initiator with `poll-netd`.
- Persistent inbox smoke sends an invite, stops the receiver before polling, restarts the sidecar, and confirms the receiver still accepts the invite from `inbox.jsonl`.
- Peer-card CLI smoke starts a real sidecar, exports a card from live health, imports it into another AMO home, and verifies the peer id/multiaddr are saved.
- Real device smoke on the same LAN delivered a Windows host room invite to a macOS peer over direct libp2p, accepted it with `poll-netd`, sent a `context_response`, and rendered it in the initiator's three-layer room context.

Latest two-node smoke result:

```json
{
  "ok": true,
  "invite_delivery_ok": true,
  "peer_accept_ok": true,
  "response_delivery_ok": true,
  "initiator_received_ok": true,
  "last_message": {
    "type": "context_response",
    "from": "node-b",
    "to": ["node-a"],
    "content": "node-b found useful memory",
    "citations": ["E-SMOKE"],
    "confidence": 0.91
  }
}
```

## References

- libp2p Go getting-started: https://libp2p.io/docs/getting-started-go/
- libp2p AutoNAT: https://libp2p.io/docs/autonat/
- libp2p circuit relay: https://libp2p.io/docs/circuit-relay/
- libp2p hole punching: https://libp2p.io/docs/hole-punching/
