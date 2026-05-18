package rendezvous

import (
	"errors"
	"sort"
	"sync"
	"time"
)

const ProtocolID = "/amo/rendezvous/1.0.0"

type PeerInfo struct {
	ID    string   `json:"peer_id"`
	Addrs []string `json:"addrs"`
}

type Request struct {
	Type       string   `json:"type"`
	Namespace  string   `json:"namespace"`
	Peer       PeerInfo `json:"peer,omitempty"`
	TTLSeconds int      `json:"ttl_seconds,omitempty"`
	Limit      int      `json:"limit,omitempty"`
	ExcludeID  string   `json:"exclude_peer_id,omitempty"`
}

type Response struct {
	OK    bool       `json:"ok"`
	Error string     `json:"error,omitempty"`
	Peers []PeerInfo `json:"peers,omitempty"`
}

type Registry struct {
	mu      sync.Mutex
	entries map[string]map[string]entry
	now     func() time.Time
}

type entry struct {
	peer      PeerInfo
	expiresAt time.Time
}

func NewRegistry() *Registry {
	return &Registry{entries: make(map[string]map[string]entry), now: time.Now}
}

func (r *Registry) Register(namespace string, peer PeerInfo, ttl time.Duration) error {
	if namespace == "" {
		return errors.New("namespace is required")
	}
	if peer.ID == "" {
		return errors.New("peer_id is required")
	}
	if len(peer.Addrs) == 0 {
		return errors.New("at least one peer address is required")
	}
	if ttl <= 0 {
		ttl = 2 * time.Hour
	}
	if ttl > 72*time.Hour {
		ttl = 72 * time.Hour
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	r.pruneLocked()
	if _, ok := r.entries[namespace]; !ok {
		r.entries[namespace] = make(map[string]entry)
	}
	r.entries[namespace][peer.ID] = entry{peer: peer, expiresAt: r.now().Add(ttl)}
	return nil
}

func (r *Registry) Discover(namespace string, excludeID string, limit int) []PeerInfo {
	if limit <= 0 {
		limit = 20
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	r.pruneLocked()
	byPeer := r.entries[namespace]
	out := make([]PeerInfo, 0, len(byPeer))
	for peerID, item := range byPeer {
		if peerID == excludeID {
			continue
		}
		out = append(out, item.peer)
	}
	sort.Slice(out, func(i, j int) bool { return out[i].ID < out[j].ID })
	if len(out) > limit {
		out = out[:limit]
	}
	return out
}

func (r *Registry) Count(namespace string) int {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.pruneLocked()
	return len(r.entries[namespace])
}

func (r *Registry) pruneLocked() {
	now := r.now()
	for namespace, byPeer := range r.entries {
		for peerID, item := range byPeer {
			if now.After(item.expiresAt) {
				delete(byPeer, peerID)
			}
		}
		if len(byPeer) == 0 {
			delete(r.entries, namespace)
		}
	}
}
