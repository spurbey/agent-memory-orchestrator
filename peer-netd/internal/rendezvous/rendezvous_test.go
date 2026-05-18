package rendezvous

import (
	"testing"
	"time"
)

func TestRegistryDiscoversPeersByNamespace(t *testing.T) {
	registry := NewRegistry()
	if err := registry.Register("team-a", PeerInfo{ID: "peer-a", Addrs: []string{"addr-a"}}, time.Hour); err != nil {
		t.Fatalf("register peer-a: %v", err)
	}
	if err := registry.Register("team-b", PeerInfo{ID: "peer-b", Addrs: []string{"addr-b"}}, time.Hour); err != nil {
		t.Fatalf("register peer-b: %v", err)
	}

	peers := registry.Discover("team-a", "", 10)
	if len(peers) != 1 || peers[0].ID != "peer-a" {
		t.Fatalf("expected only peer-a, got %#v", peers)
	}
}

func TestRegistryPrunesExpiredPeers(t *testing.T) {
	now := time.Date(2026, 5, 18, 0, 0, 0, 0, time.UTC)
	registry := NewRegistry()
	registry.now = func() time.Time { return now }

	if err := registry.Register("team-a", PeerInfo{ID: "peer-a", Addrs: []string{"addr-a"}}, time.Second); err != nil {
		t.Fatalf("register peer-a: %v", err)
	}
	now = now.Add(2 * time.Second)

	peers := registry.Discover("team-a", "", 10)
	if len(peers) != 0 {
		t.Fatalf("expected expired peer to be pruned, got %#v", peers)
	}
}
