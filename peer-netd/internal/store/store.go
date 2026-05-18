package store

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"sync"

	"github.com/agent-memory-orchestrator/peer-netd/internal/protocol"
)

type Store struct {
	mu       sync.Mutex
	messages []protocol.Envelope
	path     string
	lastErr  string
}

func New(paths ...string) *Store {
	path := ""
	if len(paths) > 0 {
		path = paths[0]
	}
	st := &Store{messages: make([]protocol.Envelope, 0), path: path}
	if path != "" {
		st.load()
	}
	return st
}

func (s *Store) Add(env protocol.Envelope) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.messages = append(s.messages, env)
	s.appendLocked(env)
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

func (s *Store) Path() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.path
}

func (s *Store) LastError() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.lastErr
}

func (s *Store) load() {
	file, err := os.Open(s.path)
	if err != nil {
		if os.IsNotExist(err) {
			return
		}
		s.lastErr = err.Error()
		return
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		line := scanner.Bytes()
		if len(line) == 0 {
			continue
		}
		var env protocol.Envelope
		if err := json.Unmarshal(line, &env); err != nil {
			s.lastErr = err.Error()
			continue
		}
		s.messages = append(s.messages, env)
	}
	if err := scanner.Err(); err != nil {
		s.lastErr = err.Error()
	}
}

func (s *Store) appendLocked(env protocol.Envelope) {
	if s.path == "" {
		return
	}
	if err := os.MkdirAll(filepath.Dir(s.path), 0o700); err != nil {
		s.lastErr = err.Error()
		return
	}
	file, err := os.OpenFile(s.path, os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		s.lastErr = err.Error()
		return
	}
	defer file.Close()
	encoded, err := json.Marshal(env)
	if err != nil {
		s.lastErr = err.Error()
		return
	}
	if _, err := file.Write(append(encoded, '\n')); err != nil {
		s.lastErr = err.Error()
		return
	}
	s.lastErr = ""
}
