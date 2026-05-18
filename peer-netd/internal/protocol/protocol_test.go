package protocol

import "testing"

func TestSignedEnvelopeVerifies(t *testing.T) {
	msg := Message{
		Type:     "peer_response",
		RoomID:   "room-1",
		FromNode: "node-a",
		ToNode:   "node-b",
		Payload: map[string]any{
			"answer": "local-first peer result",
		},
		Citations: []string{"E0001"},
	}

	env, err := NewEnvelope(msg, "test-secret")
	if err != nil {
		t.Fatalf("NewEnvelope() error = %v", err)
	}
	if env.Signature == "" {
		t.Fatal("expected signed envelope")
	}
	if err := VerifyEnvelope(env, "test-secret", true); err != nil {
		t.Fatalf("VerifyEnvelope() error = %v", err)
	}
}

func TestEnvelopeTamperFails(t *testing.T) {
	msg := Message{
		Type:     "peer_response",
		FromNode: "node-a",
		Payload:  map[string]any{"answer": "before"},
	}

	env, err := NewEnvelope(msg, "test-secret")
	if err != nil {
		t.Fatalf("NewEnvelope() error = %v", err)
	}
	env.Message.Payload["answer"] = "after"

	if err := VerifyEnvelope(env, "test-secret", true); err == nil {
		t.Fatal("expected tampered payload to fail verification")
	}
}

func TestRequiredSignatureRejectsUnsignedEnvelope(t *testing.T) {
	msg := Message{
		Type:     "peer_response",
		FromNode: "node-a",
		Payload:  map[string]any{"answer": "unsigned"},
	}

	env, err := NewEnvelope(msg, "")
	if err != nil {
		t.Fatalf("NewEnvelope() error = %v", err)
	}
	if env.Signature != "" {
		t.Fatal("expected unsigned envelope")
	}

	if err := VerifyEnvelope(env, "test-secret", true); err == nil {
		t.Fatal("expected required signature check to reject unsigned envelope")
	}
}
