package p2p

import (
	"context"
	"testing"
	"time"

	"github.com/agent-memory-orchestrator/peer-netd/internal/config"
	"github.com/agent-memory-orchestrator/peer-netd/internal/protocol"
	"github.com/agent-memory-orchestrator/peer-netd/internal/rendezvous"
	"github.com/agent-memory-orchestrator/peer-netd/internal/store"
)

func TestNodeConnectAndSendSignedMessage(t *testing.T) {
	ctx := context.Background()
	secret := "integration-secret"

	storeA := store.New()
	nodeA, err := New(ctx, testConfig("node-a", secret), storeA)
	if err != nil {
		t.Fatalf("New(node-a) error = %v", err)
	}
	defer nodeA.Close()

	storeB := store.New()
	nodeB, err := New(ctx, testConfig("node-b", secret), storeB)
	if err != nil {
		t.Fatalf("New(node-b) error = %v", err)
	}
	defer nodeB.Close()

	if len(nodeA.Addrs()) == 0 {
		t.Fatal("node-a has no dialable addrs")
	}
	if err := nodeB.Connect(ctx, nodeA.Addrs()[0]); err != nil {
		t.Fatalf("Connect() error = %v", err)
	}

	msg := protocol.Message{
		Type:     "peer_response",
		RoomID:   "room-1",
		FromNode: "node-b",
		ToNode:   "node-a",
		Payload: map[string]any{
			"answer": "peer memory found the matching decision",
		},
		Citations: []string{"E0042"},
	}
	env, err := nodeB.Send(ctx, nodeA.PeerID(), msg)
	if err != nil {
		t.Fatalf("Send() error = %v", err)
	}
	if env.Signature == "" {
		t.Fatal("sent envelope is not signed")
	}

	received := waitForMessages(t, storeA, 1)
	if got := received[0].Message.Payload["answer"]; got != "peer memory found the matching decision" {
		t.Fatalf("received payload answer = %v", got)
	}
	if got := received[0].Message.Citations[0]; got != "E0042" {
		t.Fatalf("received citation = %v", got)
	}
}

func TestRendezvousDiscoveryConnectsPeers(t *testing.T) {
	ctx := context.Background()
	secret := "rendezvous-secret"

	rendezvousStore := store.New()
	rendezvousCfg := testConfig("rv", secret)
	rendezvousCfg.EnableRendezvous = true
	rendezvousNode, err := New(ctx, rendezvousCfg, rendezvousStore)
	if err != nil {
		t.Fatalf("New(rendezvous) error = %v", err)
	}
	defer rendezvousNode.Close()

	storeA := store.New()
	nodeA, err := New(ctx, testConfig("node-a", secret), storeA)
	if err != nil {
		t.Fatalf("New(node-a) error = %v", err)
	}
	defer nodeA.Close()

	storeB := store.New()
	nodeB, err := New(ctx, testConfig("node-b", secret), storeB)
	if err != nil {
		t.Fatalf("New(node-b) error = %v", err)
	}
	defer nodeB.Close()

	rendezvousAddr := rendezvousNode.Addrs()[0]
	if err := nodeA.RegisterWithRendezvous(ctx, rendezvousAddr, "amo-test", time.Hour); err != nil {
		t.Fatalf("node-a register error = %v", err)
	}
	if err := nodeB.RegisterWithRendezvous(ctx, rendezvousAddr, "amo-test", time.Hour); err != nil {
		t.Fatalf("node-b register error = %v", err)
	}

	peers, err := nodeB.DiscoverViaRendezvous(ctx, rendezvousAddr, "amo-test", 10, true)
	if err != nil {
		t.Fatalf("discover error = %v", err)
	}
	if !containsPeer(peers, nodeA.PeerID()) {
		t.Fatalf("expected discovery to include node-a peer id %s: %#v", nodeA.PeerID(), peers)
	}

	msg := protocol.Message{
		Type:     "peer_response",
		RoomID:   "rendezvous-room",
		FromNode: "node-b",
		ToNode:   "node-a",
		Payload:  map[string]any{"answer": "rendezvous discovered node-a"},
	}
	if _, err := nodeB.Send(ctx, nodeA.PeerID(), msg); err != nil {
		t.Fatalf("send after rendezvous discovery error = %v", err)
	}
	received := waitForMessages(t, storeA, 1)
	if got := received[0].Message.Payload["answer"]; got != "rendezvous discovered node-a" {
		t.Fatalf("received payload answer = %v", got)
	}
}

func TestRelayAddressAllowsPeerMessageDelivery(t *testing.T) {
	ctx := context.Background()
	secret := "relay-secret"

	relayCfg := testConfig("relay", secret)
	relayCfg.EnableRelayService = true
	relayCfg.ForcePublic = true
	relayCfg.AdvertiseLocalhostDNS = true
	relayNode, err := New(ctx, relayCfg, store.New())
	if err != nil {
		t.Fatalf("New(relay) error = %v", err)
	}
	defer relayNode.Close()
	waitForProtocol(t, relayNode, "/libp2p/circuit/relay/0.2.0/hop")

	storeA := store.New()
	nodeACfg := testConfig("node-a", secret)
	nodeACfg.ForcePrivate = true
	nodeACfg.EnableHolePunching = true
	nodeACfg.StaticRelayAddrs = []string{relayNode.Addrs()[0]}
	nodeA, err := New(ctx, nodeACfg, storeA)
	if err != nil {
		t.Fatalf("New(node-a) error = %v", err)
	}
	defer nodeA.Close()
	if err := nodeA.Connect(ctx, relayNode.Addrs()[0]); err != nil {
		t.Fatalf("node-a connect relay error = %v", err)
	}

	relayAddr, err := nodeA.ReserveRelay(ctx, relayNode.Addrs()[0])
	if err != nil {
		t.Fatalf("ReserveRelay() error = %v", err)
	}
	if !containsString(nodeA.RelayAddrs(), relayAddr) {
		t.Fatalf("relay address not tracked: %s in %v", relayAddr, nodeA.RelayAddrs())
	}

	storeB := store.New()
	nodeBCfg := testConfig("node-b", secret)
	nodeBCfg.DialTimeout = 20 * time.Second
	nodeB, err := New(ctx, nodeBCfg, storeB)
	if err != nil {
		t.Fatalf("New(node-b) error = %v", err)
	}
	defer nodeB.Close()

	if err := nodeB.Connect(ctx, relayAddr); err != nil {
		t.Fatalf("Connect(relay addr) error = %v", err)
	}

	msg := protocol.Message{
		Type:     "peer_response",
		RoomID:   "relay-room",
		FromNode: "node-b",
		ToNode:   "node-a",
		Payload:  map[string]any{"answer": "relay path reached node-a"},
	}
	if _, err := nodeB.Send(ctx, nodeA.PeerID(), msg); err != nil {
		t.Fatalf("send through relay path error = %v", err)
	}
	received := waitForMessages(t, storeA, 1)
	if got := received[0].Message.Payload["answer"]; got != "relay path reached node-a" {
		t.Fatalf("received payload answer = %v", got)
	}
}

func testConfig(nodeID string, secret string) config.Config {
	cfg := config.Default()
	cfg.NodeID = nodeID
	cfg.SharedSecret = secret
	cfg.RequireSignature = true
	cfg.DialTimeout = 5 * time.Second
	return cfg
}

func waitForProtocol(t *testing.T, node *Node, protocolID string) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		for _, item := range node.host.Mux().Protocols() {
			if string(item) == protocolID {
				return
			}
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for protocol %s", protocolID)
}

func containsPeer(peers []rendezvous.PeerInfo, peerID string) bool {
	for _, item := range peers {
		if item.ID == peerID {
			return true
		}
	}
	return false
}

func waitForMessages(t *testing.T, st *store.Store, want int) []protocol.Envelope {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if st.Count() >= want {
			return st.List()
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %d messages; got %d", want, st.Count())
	return nil
}
