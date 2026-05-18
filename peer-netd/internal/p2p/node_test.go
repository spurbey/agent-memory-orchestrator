package p2p

import (
	"context"
	"testing"
	"time"

	"github.com/agent-memory-orchestrator/peer-netd/internal/config"
	"github.com/agent-memory-orchestrator/peer-netd/internal/protocol"
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

func testConfig(nodeID string, secret string) config.Config {
	cfg := config.Default()
	cfg.NodeID = nodeID
	cfg.SharedSecret = secret
	cfg.RequireSignature = true
	cfg.DialTimeout = 5 * time.Second
	return cfg
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
