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
  netd_client.py
  netd_runtime.py
```

`netd_client.py` is the Python bridge. It talks to `amo-peer-netd` over localhost HTTP and converts AMO peer-room messages into sidecar send requests.

`netd_runtime.py` is the managed sidecar lifecycle layer. It builds the Go binary into `AMO_HOME/.peer/bin`, starts/stops it, writes PID/API/log state under `AMO_HOME/.peer/netd`, and refuses unsafe managed starts where the local API port is dynamic.

## Managed User Flow

The intended user path is AMO-owned, not Tailscale-owned:

```powershell
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> init --node-id zenbook-amo
$env:AMO_PEER_NETD_SECRET="<shared-secret>"
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> enable `
  --node-id zenbook-amo `
  --api 127.0.0.1:8788 `
  --shared-secret-env AMO_PEER_NETD_SECRET `
  --require-signature
```

Operational commands:

```powershell
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd build
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd start --node-id zenbook-amo
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd status
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd stop
```

`peer enable` is the one-command normal path. It builds the sidecar if needed, starts it, waits for `/health`, and returns the peer id/listen addresses. Future install work should wire this into a background OS service, but the process state is already AMO-owned.

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

The current implementation supports explicit multiaddr dialing, static bootstrap dialing, LAN mDNS, managed process start/stop, relay reservation, and an AMO rendezvous stream protocol. Full installer/service wiring is still the next packaging step.

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
- CLI tests verify `peer netd status` uses `--amo-home` and `peer enable` rejects dynamic API ports before building.
- Binary smoke starts three real sidecar processes: rendezvous, node A, and node B. A/B register, B discovers A, B sends a signed response, and A receives it.
- Binary relay smoke starts three real sidecar processes: relay, private node A, and node B. A reserves a relay slot, B dials A's `/p2p-circuit` address, B sends a signed response, and A receives it.
- Managed runtime smoke starts the sidecar through `python -m agent_memory_orchestrator.app.cli peer enable`, checks `peer netd status`, then stops it through `peer netd stop`.

## References

- libp2p Go getting-started: https://libp2p.io/docs/getting-started-go/
- libp2p AutoNAT: https://libp2p.io/docs/autonat/
- libp2p circuit relay: https://libp2p.io/docs/circuit-relay/
- libp2p hole punching: https://libp2p.io/docs/hole-punching/
