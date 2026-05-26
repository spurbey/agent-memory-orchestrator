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

scripts/
  build_peer_netd_binaries.py
```

`cards.py` builds and imports peer-card JSON so users do not manually copy libp2p ids and multiaddrs into commands.

`doctor.py` produces the operator readiness report for peer identity, packaged `peer-netd` source, binary/build state, sidecar health, trusted peers, and shared-secret environment variables.

`netd_client.py` is the Python bridge. It talks to `amo-peer-netd` over localhost HTTP and converts AMO peer-room messages into sidecar send requests.

`netd_runtime.py` is the managed sidecar lifecycle layer. It locates repo or packaged `peer-netd` source, builds the Go binary into `AMO_HOME/.peer/bin`, starts/stops it, writes PID/API/log state under `AMO_HOME/.peer/netd`, and refuses unsafe managed starts where the local API port is dynamic.

Managed starts persist the libp2p private key at `AMO_HOME/.peer/netd/identity.key` by default. This keeps peer IDs and relay multiaddrs stable across restarts.

`netd_service.py` plans OS startup integration. It returns a Windows Scheduled Task plan or user-systemd unit plan by default, and only mutates the host when `--apply` is explicitly used.

`scripts/build_peer_netd_binaries.py` builds release binaries into `src/agent_memory_orchestrator/bin/<goos-goarch>/`. Wheel/package builds include those files when present, so normal users do not need Go once release packaging generates platform binaries.

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
python -m agent_memory_orchestrator.app.cli peer-agent --amo-home <amo_home> watch
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd stop
```

`peer doctor` is the first diagnostic command for a new machine. It separates blocking install/config failures from normal next steps like starting the sidecar or importing peer cards.

`peer enable` is the low-level normal path. It uses a packaged prebuilt sidecar when available, otherwise builds the sidecar if Go is available, starts it, waits for `/health`, and returns the peer id/listen addresses. It also verifies that an existing sidecar binary supports the current required flags before launch; stale binaries are replaced from a packaged binary or rebuilt from source. Packaged installs include the Go sidecar source so users do not need a repo clone just to build `amo-peer-netd`.

`peer setup` is the user-facing one-time path. It initializes the peer identity, expands a saved relay profile, starts the sidecar, can accept an invite, and can install per-user startup entries for both peer netd and `peer-agent watch`.

Delivered envelopes are persisted by default:

```text
AMO_HOME/.peer/netd/inbox.jsonl
```

If the sidecar receives a message and restarts before AMO runs `peer-agent watch`, the inbox reloads from this JSONL file.

For active participation, run `peer-agent watch` beside the sidecar. It continuously drains delivered sidecar messages into AMO peer-room state, applies policy, retrieves local memory for trusted context requests, sends responses, summarizes initiator rooms, and finalizes timed-out rooms. `peer poll-netd --watch` remains a low-level transport-debug command.

Startup planning:

```powershell
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd install-service --node-id zenbook-amo
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd install-service --node-id zenbook-amo --with-watch
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd install-service --node-id zenbook-amo --with-watch --apply
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd service-status --with-watch
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd uninstall-service --with-watch --apply
```

The default is a plan, not mutation. On Windows the apply path creates per-user Scheduled Tasks at logon. On macOS it writes user `launchd` LaunchAgents. On Linux it writes user systemd units and enables them. Use `--with-watch` for the normal bot-participation setup: one startup entry keeps `amo-peer-netd` online, and the second runs `peer-agent watch` so trusted room invites, memory requests, responses, summaries, and finalization are processed without manual polling.

Short relay setup:

```powershell
python -m agent_memory_orchestrator.app.cli peer --amo-home <home_a> relay save --name amo-test --addr <relay_multiaddr> --namespace amo-test
python -m agent_memory_orchestrator.app.cli peer --amo-home <home_a> setup --node-id node-a --display-name "Node A" --relay amo-test --install-startup
python -m agent_memory_orchestrator.app.cli peer --amo-home <home_a> create-invite --auto-approve --relay amo-test --out node-a.invite.json
python -m agent_memory_orchestrator.app.cli peer --amo-home <home_b> setup --node-id node-b --display-name "Node B" --invite node-a.invite.json --install-startup
```

The accepting setup command reads the invite's rendezvous fields, saves the relay profile locally, starts its sidecar through that relay, then accepts the invite and sends the join request back when the initiator is reachable.

After startup is installed on both devices, repeated usage should stay at the bot level:

```powershell
python -m agent_memory_orchestrator.app.cli peer-agent --amo-home <home_a> ask --query "<question>"
```

Users should not need to run `peer-agent watch`, `poll-netd`, `open-room`, or `send-message` during normal operation. Those commands remain useful for debugging and low-level smoke tests.

If a device has old test peers configured, remove them once so future asks do not waste time dialing stale reservations:

```powershell
python -m agent_memory_orchestrator.app.cli peer --amo-home <home_a> remove --node-id <old-peer-node-id>
```

To ask only one trusted peer without changing config:

```powershell
python -m agent_memory_orchestrator.app.cli peer-agent --amo-home <home_a> ask --peer node-b --query "<question>"
```

## Room Flow Over Netd

Configure peers with peer cards where possible:

```powershell
python -m agent_memory_orchestrator.app.cli peer --amo-home <home_b> share-card --out node-b.card.json
python -m agent_memory_orchestrator.app.cli peer --amo-home <home_a> import-card --file node-b.card.json
```

For lower-friction onboarding, use an invite code/bundle:

```powershell
python -m agent_memory_orchestrator.app.cli peer --amo-home <home_a> create-invite --auto-approve --out node-a.invite.json
python -m agent_memory_orchestrator.app.cli peer --amo-home <home_b> accept-invite --file node-a.invite.json
python -m agent_memory_orchestrator.app.cli peer-agent --amo-home <home_a> watch --max-iterations 1
```

The invite wraps the inviter's public peer card, recommended trust level, one-time invite token, and a card hash. It never contains a shared-secret value. If both sidecars are running, `accept-invite` sends a `peer_join_request` back to the inviter. With `--auto-approve`, the inviter imports the accepting peer after token validation. Without `--auto-approve`, review the request explicitly:

```powershell
python -m agent_memory_orchestrator.app.cli peer --amo-home <home_a> join-requests --status pending
python -m agent_memory_orchestrator.app.cli peer --amo-home <home_a> approve-join --request-id <request_id>
```

Manual config still exists for smoke tests and advanced use:

```powershell
python -m agent_memory_orchestrator.app.cli peer --amo-home <home_a> add --node-id node-b --peer-id <node_b_libp2p_peer_id> --multiaddr <node_b_multiaddr>
```

Then the low-level room smoke path is:

```powershell
python -m agent_memory_orchestrator.app.cli peer --amo-home <home_a> open-room --topic "..." --peer node-b
python -m agent_memory_orchestrator.app.cli peer-agent --amo-home <home_b> watch
python -m agent_memory_orchestrator.app.cli peer --amo-home <home_b> send-message --room-id <room_id> --peer-id node-a --type context_response --content "..." --citation E0001 --confidence 0.91
python -m agent_memory_orchestrator.app.cli peer-agent --amo-home <home_a> watch --max-iterations 1
```

`peer-agent watch` uses the managed sidecar API URL from `AMO_HOME/.peer/netd/netd.json`, so each AMO home can run on a different local API port without extra environment variables. The lower-level `peer poll-netd` command is still useful when you only want to drain envelopes without running memory retrieval or agent finalization.

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

The current implementation supports explicit multiaddr dialing, peer-card export/import, invite bundle/code trust exchange, packaged sidecar source discovery, packaged prebuilt binary discovery, readiness diagnostics, static bootstrap dialing, LAN mDNS, managed process start/stop, persistent sidecar inbox, watched peer-agent draining, sidecar-backed room invites/messages, relay reservation, an AMO rendezvous stream protocol, and OS startup planning for both netd and the AMO peer-agent watcher.

Incoming room invites and messages remain local-policy gated. Invites require a trusted initiator under `trusted_only`; messages require a trusted configured sender that is already a room participant. If a peer has `shared_secret_env` configured, netd-delivered messages from that peer must be authenticated or AMO rejects them before mutating room state.

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

## Public Relay/Rendezvous Deployment

For peers on different routers or subnets, run one small always-on helper node. This is the AMO equivalent of the minimum Tailscale control-plane replacement: it does discovery and relay only, not memory or room state.

On a public VPS or always-on machine with inbound TCP open:

```powershell
amo-cli peer relay start `
  --node-id amo-relay-prod `
  --listen /ip4/0.0.0.0/tcp/4001 `
  --advertise-addr /ip4/<public_ip>/tcp/4001 `
  --namespace <team_namespace>
```

Use `/dns4/<domain>/tcp/4001` instead of `/ip4/<public_ip>/tcp/4001` if DNS is stable. The command starts `amo-peer-netd` with rendezvous, relay service, NAT service, and public reachability defaults. It prints the relay multiaddr that clients should use.

On each user device, save a relay profile once, then start local peer netd with the short profile name:

```powershell
amo-cli peer relay save --name amo-team --addr <relay_multiaddr> --namespace <team_namespace>
amo-cli peer setup --node-id <device_node_id> --relay amo-team --install-startup
```

Then create invites with the same relay profile:

```powershell
amo-cli peer create-invite --auto-approve --relay amo-team --out host.invite.json
```

The important ordering is: start the local sidecar through the relay, confirm `peer netd status` shows `relay_addrs`, then create or accept invites. The peer card inside the invite/response will include `/p2p-circuit` relay addresses, so the join request can return even when direct LAN dialing fails. The long-form `--static-relay --auto-relay --hole-punching --rendezvous-*` flags remain available for debugging and automation.

For production operations, run at least two helper nodes and pass both as `--static-relay` values. Monitor process liveness, open TCP port reachability, relay reservation failures, and bandwidth. The helper should reject broad public access later with invite/group-level admission rules; until then, treat it as a private beta service.

AWS deployment automation lives in `docs/operations/aws-peer-relay.md` and `infra/aws/peer-relay/cloudformation.yaml`. It creates the small EC2/EIP/SSM helper node and prints the relay/rendezvous flags clients should use.

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
- CLI tests verify libp2p peer config, peer-card export/import, inbox polling/watch behavior, and startup service planning.
- Peer-room policy tests verify untrusted senders, non-participant senders, and unsigned netd messages for secret-required peers are rejected.
- Wheel install smoke verifies packaged installs contain the `peer-netd` Go source tree and `PeerNetdRuntime` can discover it outside the repo.
- Prebuilt wheel smoke verifies a generated `amo-peer-netd` binary is included in the wheel and discovered by installed runtime.
- Go store tests verify delivered envelopes persist to JSONL and reload after restart.
- Binary smoke starts three real sidecar processes: rendezvous, node A, and node B. A/B register, B discovers A, B sends a signed response, and A receives it.
- Binary relay smoke starts three real sidecar processes: relay, private node A, and node B. A reserves a relay slot, B dials A's `/p2p-circuit` address, B sends a signed response, and A receives it.
- Managed runtime smoke starts the sidecar through `python -m agent_memory_orchestrator.app.cli peer enable`, checks `peer netd status`, then stops it through `peer netd stop`.
- Two-node room smoke starts two sidecars with two separate AMO homes, sends `open-room` invite over libp2p, accepts it with `poll-netd`, sends a `context_response`, and ingests it on the initiator with `poll-netd`.
- Peer-agent smoke starts two sidecars with two separate AMO homes, imports live peer cards, runs `peer-agent watch` on the peer, runs `peer-agent ask` on the initiator, and verifies the peer response reaches the initiator context.
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
