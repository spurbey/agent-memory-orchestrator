package store

import (
	"path/filepath"
	"testing"

	"github.com/agent-memory-orchestrator/peer-netd/internal/protocol"
)

func TestStorePersistsAndReloadsMessages(t *testing.T) {
	path := filepath.Join(t.TempDir(), "inbox.jsonl")
	first := New(path)
	first.Add(protocol.Envelope{
		Version:    protocol.EnvelopeVersion,
		FromNodeID: "node-a",
		Message: protocol.Message{
			Type:     "context_response",
			RoomID:   "room-1",
			FromNode: "node-a",
			Payload:  map[string]any{"content": "persisted"},
		},
	})

	second := New(path)
	messages := second.List()

	if len(messages) != 1 {
		t.Fatalf("expected 1 message after reload, got %d", len(messages))
	}
	if messages[0].Message.Type != "context_response" {
		t.Fatalf("unexpected message type: %s", messages[0].Message.Type)
	}
	if second.Path() != path {
		t.Fatalf("unexpected path: %s", second.Path())
	}
	if second.LastError() != "" {
		t.Fatalf("unexpected store error: %s", second.LastError())
	}
}

func TestStoreAllowsInMemoryMode(t *testing.T) {
	st := New()
	st.Add(protocol.Envelope{Version: protocol.EnvelopeVersion, FromNodeID: "node-a"})

	if st.Path() != "" {
		t.Fatalf("expected empty path")
	}
	if st.Count() != 1 {
		t.Fatalf("expected in-memory message")
	}
}
