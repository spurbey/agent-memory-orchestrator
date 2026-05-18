package store

import (
	"sync"

	"github.com/agent-memory-orchestrator/peer-netd/internal/protocol"
)

type Store struct {
	mu       sync.Mutex
	messages []protocol.Envelope
}

func New() *Store {
	return &Store{messages: make([]protocol.Envelope, 0)}
}

func (s *Store) Add(env protocol.Envelope) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.messages = append(s.messages, env)
}

func (s *Store) List() []protocol.Envelope {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]protocol.Envelope, len(s.messages))
	copy(out, s.messages)
	return out
}

func (s *Store) Count() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.messages)
}
