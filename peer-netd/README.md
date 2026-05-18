# AMO Peer Netd

`amo-peer-netd` is the libp2p transport sidecar for AMO peer rooms. It is intentionally small: Python AMO keeps memory retrieval, room policy, summarization, and context windows; the sidecar only handles peer reachability and message delivery.

## Layout

```text
peer-netd/
  cmd/amo-peer-netd/        # sidecar binary entrypoint
  internal/config/          # runtime flags and defaults
  internal/localapi/        # localhost API used by Python AMO
  internal/p2p/             # go-libp2p host, connect, send, receive
  internal/protocol/        # AMO message envelope, hash, HMAC verification
  internal/rendezvous/      # AMO namespace registration and peer discovery
  internal/store/           # local in-memory inbox for delivered messages
```

## Local Smoke

```powershell
..\.tmp\tools\go\bin\go.exe test ./...
```

Build:

```powershell
..\.tmp\tools\go\bin\go.exe build -o ..\.tmp\bin\amo-peer-netd.exe .\cmd\amo-peer-netd
```

Run a node:

```powershell
..\.tmp\bin\amo-peer-netd.exe `
  --node-id node-a `
  --listen /ip4/127.0.0.1/tcp/0 `
  --api 127.0.0.1:8788 `
  --shared-secret "<room-or-peer-secret>" `
  --require-signature
```

The first stdout line is JSON containing the libp2p `peer_id`, dialable `listen_addrs`, and local `api_addr`.

## Managed From AMO

Normal users should not launch this binary directly. AMO owns the process lifecycle:

```powershell
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd build
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> enable --node-id zenbook-amo
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd status
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd stop
```

The managed runtime writes:

```text
AMO_HOME/.peer/bin/amo-peer-netd(.exe)
AMO_HOME/.peer/netd/netd.json
AMO_HOME/.peer/netd/logs/*.log
```

Use the direct binary commands only for sidecar development and low-level libp2p debugging.

Plan OS startup:

```powershell
python -m agent_memory_orchestrator.app.cli peer --amo-home <amo_home> netd install-service --node-id zenbook-amo
```

Add `--apply` only when you want AMO to create the Windows Scheduled Task or user-systemd unit.

## API

```text
GET  /health
POST /connect   {"addr": "<multiaddr-with-/p2p/<peer_id>>"}
POST /send      {"to_peer_id": "...", "message": {...}}
GET  /messages
GET  /peers
POST /bootstrap              {"addrs": ["<peer multiaddr>"]}
POST /relay/reserve          {"addrs": ["<relay multiaddr>"]}
POST /rendezvous/register    {"addr": "<rendezvous multiaddr>", "namespace": "amo-team"}
POST /rendezvous/discover    {"addr": "<rendezvous multiaddr>", "namespace": "amo-team", "connect": true}
```

Messages are wrapped in AMO envelopes. When a shared secret is configured, outbound envelopes are signed with HMAC-SHA256 and inbound messages can require signatures.

## Current Scope

Implemented:

- local libp2p host startup
- explicit peer dialing by multiaddr
- static bootstrap dialing
- explicit circuit-v2 relay reservation
- LAN mDNS discovery
- AMO rendezvous server/client discovery over libp2p streams
- signed AMO envelope send/receive
- localhost HTTP API for Python AMO
- AMO-managed build/start/stop/status runtime
- AMO startup-service planning through CLI
- unit tests for envelope verification
- integration test for node-to-node delivery
- integration test for rendezvous discovery plus message delivery
- integration test for relay reservation plus message delivery
- binary smoke with two sidecar processes
- binary smoke with rendezvous plus two peer processes
- binary smoke with relay service plus two peer processes

Not implemented yet:

- NAT reachability status
- packaged installer integration for the service planner
- persistent inbox storage
