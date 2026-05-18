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
  internal/store/store.go

src/agent_memory_orchestrator/peer/
  netd_client.py
```

`netd_client.py` is the Python bridge. It talks to `amo-peer-netd` over localhost HTTP and converts AMO peer-room messages into sidecar send requests.

## Implemented Flow

```text
peer enable starts amo-peer-netd
node prints peer_id and listen_addrs
AMO connects to a peer multiaddr
AMO sends signed peer_response
remote sidecar verifies envelope
remote AMO reads /messages and processes room response
```

The first implemented slice intentionally uses explicit multiaddrs. The next slice should add discovery so users do not copy addresses.

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

## References

- libp2p Go getting-started: https://libp2p.io/docs/getting-started-go/
- libp2p AutoNAT: https://libp2p.io/docs/autonat/
- libp2p circuit relay: https://libp2p.io/docs/circuit-relay/
- libp2p hole punching: https://libp2p.io/docs/hole-punching/
